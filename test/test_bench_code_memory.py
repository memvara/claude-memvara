"""Tests for bench/code_memory.py, the Phase 0 harness of the code project memory design.

They need neither the network nor `claude`. The transcripts below are written by hand in
the shape that `claude -p --output-format stream-json --verbose` produced on 2026-10-15,
with Claude Code 2.1.289, and `run` is driven through a stand-in `claude` on PATH.
"""

import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import code_memory as cm  # noqa: E402


def init(session="s1", memvara=None):
    plugins, servers = [], []
    if memvara:
        plugins = [{"name": "memvara", "source": memvara[0]}]
        servers = [{"name": "plugin:memvara:memvara", "status": memvara[1]}]
    return {"type": "system", "subtype": "init", "session_id": session,
            "plugins": plugins, "mcp_servers": servers}


def reply(message_id, *blocks, context=1000, parent=None):
    """One reply, sent the way Claude Code sends it: one event per content block, each
    carrying the usage of the whole reply."""
    usage = {"input_tokens": 10, "cache_creation_input_tokens": context - 10,
             "cache_read_input_tokens": 0, "output_tokens": 4}
    return [{"type": "assistant", "parent_tool_use_id": parent,
             "message": {"id": message_id, "usage": usage, "content": [block]}}
            for block in blocks]


def call(name, **args):
    return {"type": "tool_use", "name": name, "input": args}


TEXT = {"type": "text", "text": "thinking aloud"}


def result(text="the answer", cost=0.5, tokens=5000, error=False):
    event = {"type": "result", "total_cost_usd": cost, "is_error": error,
             "modelUsage": {"claude-sonnet": {"inputTokens": tokens, "outputTokens": 0,
                                              "cacheReadInputTokens": 0,
                                              "cacheCreationInputTokens": 0}}}
    if text is not None:
        event["result"] = text
    return event


def lines(*events):
    out = []
    for event in events:
        out += [json.dumps(e) for e in (event if isinstance(event, list) else [event])]
    return out


SNAP = "/data/snapshots/memvara"


class ParseRun(unittest.TestCase):

    def parse(self, *events, expected=("memvara/store.py",)):
        return cm.parse_run(lines(*events), list(expected), SNAP)

    def test_a_reply_split_into_blocks_counts_its_usage_once(self):
        run = self.parse(init(), reply("m1", TEXT, TEXT, context=1000),
                         reply("m2", call("Read", file_path=f"{SNAP}/memvara/store.py"),
                               context=2000), result())
        self.assertEqual(run.tokens_to_first, 3000)

    def test_the_first_call_that_reads_an_expected_file_is_the_one_counted(self):
        run = self.parse(init(), reply("m1", call("Grep", pattern="def store"),
                                       call("Glob", pattern="**/*.py"),
                                       call("Read", file_path=f"{SNAP}/memvara/core.py")),
                         reply("m2", call("Read", file_path=f"{SNAP}/memvara/store.py"),
                               call("Read", file_path=f"{SNAP}/memvara/store.py")),
                         result())
        self.assertTrue(run.opened)
        self.assertEqual((run.calls_to_first, run.tool_calls), (4, 5))

    def test_calls_inside_a_subagent_count(self):
        run = self.parse(init(), reply("m1", call("Agent", prompt="find the store")),
                         reply("m2", call("Read", file_path=f"{SNAP}/memvara/store.py"),
                               parent="toolu_1"), result())
        self.assertEqual(run.calls_to_first, 2)

    def test_a_run_that_never_reads_one_counts_one_call_more_than_it_made(self):
        run = self.parse(init(), reply("m1", call("Grep", pattern="x"), call("Glob", pattern="y"),
                                       call("Read", file_path=f"{SNAP}/README.md")), result())
        self.assertFalse(run.opened)
        self.assertEqual(run.calls_to_first, 4)

    def test_a_run_without_tool_calls_still_counts_as_one_call_short(self):
        run = self.parse(init(), reply("m1", TEXT), result())
        self.assertEqual((run.tool_calls, run.calls_to_first), (0, 1))

    def test_only_whole_paths_match(self):
        cases = [
            (call("Read", file_path=f"{SNAP}/memvara/restore.py"), False),
            (call("Read", file_path="/elsewhere/memvara/store.py"), False),
            (call("Read", file_path=f"{SNAP}/memvara/store.py"), True),
            (call("Grep", pattern="x", path="memvara/store.py"), True),
            (call("Bash", command="wc -l memvara/store.pyc"), False),
            (call("Bash", command="cat legacy-memvara/store.py"), False),
            (call("Bash", command=f"wc -l {SNAP}/memvara/store.py"), True),
            (call("Bash", command="git log --oneline -- memvara/store.py"), True),
            (call("mcp__plugin_memvara_memvara__memory_search", query="memvara/store.py"), False),
        ]
        for block, matches in cases:
            with self.subTest(block=block):
                self.assertEqual(cm.touches(block, ["memvara/store.py"], SNAP), matches)

    def test_a_run_that_stopped_at_its_budget_still_parses(self):
        run = self.parse(init(), reply("m1", call("Grep", pattern="x")),
                         result(text=None, error=True, cost=2.01))
        self.assertTrue(run.error)
        self.assertEqual((run.answer, run.cost_usd), ("", 2.01))

    def test_the_result_gives_the_answer_the_cost_and_every_token(self):
        run = self.parse(init(), reply("m1", TEXT), result(text="ok", cost=0.25, tokens=1234))
        self.assertEqual((run.answer, run.cost_usd, run.tokens), ("ok", 0.25, 1234))

    def test_a_cut_off_last_line_is_left_out(self):
        # A run stopped mid-write leaves half a JSON object as its last line.
        cut = lines(init(), reply("m1", call("Grep", pattern="x")), result())
        cut[-1] = cut[-1][:20]
        run = cm.parse_run(cut, ["memvara/store.py"], SNAP)
        self.assertEqual((run.session, run.tool_calls, run.answer), ("s1", 1, ""))

    def test_memvara_is_seen_in_the_init_event(self):
        self.assertEqual(self.parse(init(), result()).memvara, "")
        loaded = self.parse(init(memvara=("memvara@inline", "connected")), result())
        self.assertEqual(loaded.memvara, "memvara@inline connected")


class Gate(unittest.TestCase):

    def summary(self, calls, right, tokens, why=10):
        return cm.Summary("X", 40, calls, 0, tokens, 0.0, right, 40, why_questions=why)

    def test_thirty_percent_fewer_calls_passes_within_a_fifth_more_tokens(self):
        self.assertTrue(cm.gate(self.summary(10, 3, 100), self.summary(7, 3, 120))[0])

    def test_more_than_a_fifth_more_tokens_fails_whatever_else_improved(self):
        self.assertFalse(cm.gate(self.summary(10, 3, 100), self.summary(5, 9, 121))[0])

    def test_two_more_decision_questions_right_passes(self):
        self.assertTrue(cm.gate(self.summary(10, 3, 100), self.summary(10, 5, 100))[0])
        self.assertFalse(cm.gate(self.summary(10, 3, 100), self.summary(8, 4.5, 100))[0])

    def test_the_decision_bar_is_two_for_every_ten_why_questions(self):
        # Decided 2026-10-15: only four real why questions passed the selection rule, and the
        # spec's "2 more of 10" is kept as a share, so four questions need +0.8.
        self.assertTrue(cm.gate(self.summary(10, 1, 100, why=4), self.summary(10, 1.8, 100, why=4))[0])
        self.assertFalse(cm.gate(self.summary(10, 1, 100, why=4), self.summary(10, 1.5, 100, why=4))[0])

    def test_with_no_why_questions_the_decision_half_cannot_pass(self):
        self.assertFalse(cm.gate(self.summary(10, 0, 100, why=0), self.summary(10, 0, 100, why=0))[0])

    def test_decisions_are_averaged_over_repeats_and_only_why_questions_count(self):
        why1, why2 = {"id": "q1", "kind": "why"}, {"id": "q2", "kind": "why"}
        locate = {"id": "q3", "kind": "locate"}
        rows = [(why1, cm.Run(tokens_to_first=100), "right"),
                (why1, cm.Run(tokens_to_first=200), "wrong"),
                (why2, cm.Run(tokens_to_first=300), "right"),
                (why2, cm.Run(tokens_to_first=400), "right"),
                (locate, cm.Run(tokens_to_first=500), "right"),
                (locate, cm.Run(tokens_to_first=600), None)]
        summary = cm.summarize("A", rows)
        self.assertEqual((summary.right, summary.judged), (1.5, 5))
        self.assertEqual(summary.tokens_to_first, 350)
        self.assertEqual(summary.why_questions, 2)

    def test_runs_that_stopped_early_are_counted(self):
        q = {"id": "q1", "kind": "locate"}
        rows = [(q, cm.Run(error=True), None), (q, cm.Run(), None), (q, cm.Run(error=True), None)]
        self.assertEqual(cm.summarize("A", rows).stopped, 2)


class Setups(unittest.TestCase):

    def settings(self, cmd):
        return json.loads(cmd[cmd.index("--settings") + 1])

    def test_a_and_c_turn_memvara_off_and_load_no_copy(self):
        for arm in ("A", "C"):
            cmd = cm.command("Why?", arm)
            self.assertEqual(self.settings(cmd)["enabledPlugins"], {cm.MEMVARA: False})
            self.assertNotIn("--plugin-dir", cmd)
        self.assertNotIn("hooks", self.settings(cm.command("Why?", "A")))

    def test_b_turns_the_installed_memvara_off_and_loads_the_copy(self):
        cmd = cm.command("Why?", "B", pathlib.Path("/copy"))
        self.assertEqual(self.settings(cmd)["enabledPlugins"], {cm.MEMVARA: False})
        self.assertEqual(cmd[cmd.index("--plugin-dir") + 1], "/copy")

    def test_c_adds_the_notes_hook_at_start_and_before_file_tools(self):
        hooks = self.settings(cm.command("Why?", "C"))["hooks"]
        self.assertEqual(hooks["PreToolUse"][0]["matcher"], "Read|Edit|MultiEdit|Write")
        for event in ("SessionStart", "PreToolUse"):
            self.assertTrue(hooks[event][0]["hooks"][0]["command"].endswith("code_memory.py hook"))

    def test_every_setup_asks_the_question_and_may_change_nothing(self):
        for arm in cm.ARMS:
            cmd = cm.command("Why is it like this?", arm, pathlib.Path("/copy"))
            self.assertEqual(cmd[:3], ["claude", "-p", "Why is it like this?"])
            denied = cmd[cmd.index("--disallowedTools") + 1:]
            for tool in ("Edit", "Write", "mcp__plugin_memvara_memvara__memory_remember",
                         "mcp__plugin_memvara_memvara__memory_add"):
                self.assertIn(tool, denied)

    def test_the_copy_keeps_memvara_and_drops_capture(self):
        # Read against this repository's real plugin, so a capture hook moved to another
        # event upstream fails here rather than writing every run to the store.
        with tempfile.TemporaryDirectory() as tmp:
            copy = cm.plugin_without_capture(pathlib.Path(tmp) / "plugin-b")
            hooks = json.loads((copy / "hooks" / "hooks.json").read_text())["hooks"]
        self.assertNotIn("Stop", hooks)
        self.assertNotIn("capture", json.dumps(hooks))
        self.assertIn("SessionStart", hooks)
        self.assertIn("UserPromptSubmit", hooks)

    def test_a_run_in_the_wrong_setup_is_named(self):
        on = cm.Run(session="s", memvara="memvara@inline connected")
        installed = cm.Run(session="s", memvara="memvara@claude-memvara connected")
        off = cm.Run(session="s")
        self.assertIsNotNone(cm.check_setup("A", on, set()))
        self.assertIsNone(cm.check_setup("A", off, set()))
        self.assertIsNotNone(cm.check_setup("B", installed, set()))
        self.assertIsNotNone(cm.check_setup("B", off, set()))
        self.assertIsNone(cm.check_setup("B", on, set()))
        self.assertIsNotNone(cm.check_setup("C", off, set()))
        self.assertIsNone(cm.check_setup("C", off, {"s"}))
        self.assertIsNotNone(cm.check_setup("A", cm.Run(), set()))


class NotesHook(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = pathlib.Path(tmp.name)
        self.snap = self.data / "snapshots" / "memvara"
        (self.snap / "memvara" / "store").mkdir(parents=True)
        (self.data / "notes").mkdir()
        (self.data / "notes" / "memvara.json").write_text(json.dumps({
            "brief": "BRIEF",
            "components": {"memvara": "CORE NOTES", "memvara/store": "STORE NOTES"}}))
        patcher = mock.patch.object(cm, "DATA", self.data)
        patcher.start()
        self.addCleanup(patcher.stop)

    def touch(self, path, session="s1", tool="Read"):
        reply = cm.hook({"hook_event_name": "PreToolUse", "session_id": session,
                         "tool_name": tool, "tool_input": {"file_path": str(path)}})
        return reply and reply["hookSpecificOutput"]["additionalContext"]

    def test_the_brief_is_added_when_the_session_starts(self):
        reply = cm.hook({"hook_event_name": "SessionStart", "session_id": "s1",
                         "cwd": str(self.snap)})
        self.assertEqual(reply["hookSpecificOutput"],
                         {"hookEventName": "SessionStart", "additionalContext": "BRIEF"})
        self.assertEqual(cm.briefed(), {"s1"})

    def test_a_component_s_notes_are_added_once_per_session(self):
        self.assertEqual(self.touch(self.snap / "memvara/store/a.py"), "STORE NOTES")
        self.assertIsNone(self.touch(self.snap / "memvara/store/b.py", tool="Edit"))
        self.assertEqual(self.touch(self.snap / "memvara/core.py"), "CORE NOTES")
        self.assertEqual(self.touch(self.snap / "memvara/store/a.py", session="s2"),
                         "STORE NOTES")

    def test_a_file_outside_every_component_or_snapshot_adds_nothing(self):
        self.assertIsNone(self.touch(self.snap / "README.md"))
        self.assertIsNone(self.touch("/etc/hosts"))
        self.assertIsNone(cm.hook({"hook_event_name": "PreToolUse", "session_id": "s1",
                                   "tool_name": "Write", "tool_input": {}}))

    def test_the_hook_never_fails_and_says_why_in_its_log(self):
        (self.data / "notes" / "memvara.json").write_text("{broken")
        for stdin in ("not json",
                      json.dumps({"hook_event_name": "SessionStart", "cwd": str(self.snap)})):
            out = io.StringIO()
            with mock.patch.object(sys, "stdin", io.StringIO(stdin)), redirect_stdout(out):
                self.assertEqual(cm.run_hook(), 0)
            self.assertEqual(out.getvalue(), "")
        self.assertEqual((self.data / "hook.log").read_text().count('"error"'), 2)


class RunCommand(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = pathlib.Path(tmp.name)
        (self.data / "snapshots" / "repo").mkdir(parents=True)
        (self.data / "questions.json").write_text(json.dumps([{
            "id": "q1", "repo": "repo", "kind": "locate", "question": "Where?",
            "expected_files": ["x.py"], "answer_key": "x.py"}]))
        bin_dir = self.data / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "claude"
        fake.write_text(f"""#!{sys.executable}
import json, os
open(os.environ["FAKE_CALLS"], "a").write("x")
print(json.dumps({json.dumps(init(session="s1"))}))
print(json.dumps({{"type": "assistant", "message": {{"id": "m1", "usage": {{}},
    "content": [{{"type": "tool_use", "name": "Read",
                  "input": {{"file_path": os.path.join(os.getcwd(), "x.py")}}}}]}}}}))
print(json.dumps({json.dumps(result())}))
""")
        fake.chmod(0o755)
        self.calls = self.data / "calls"
        env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_CALLS": str(self.calls)}
        for patcher in (mock.patch.object(cm, "DATA", self.data), mock.patch.dict(os.environ, env)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_run_is_kept_once_and_not_repeated(self):
        with redirect_stdout(io.StringIO()) as out:
            cm.run(["A"], 1, None)
            cm.run(["A"], 1, None)
        self.assertTrue((self.data / "runs" / "A" / "q1-1.jsonl").exists())
        self.assertEqual(self.calls.read_text(), "x")
        self.assertIn("right file at call 1", out.getvalue())

    def test_a_run_without_its_setup_is_set_aside_and_tried_again_next_time(self):
        # The stand-in never calls the notes hook, so setup C never gets its brief.
        with redirect_stdout(io.StringIO()) as out:
            cm.run(["C"], 1, None)
            cm.run(["C"], 1, None)
        self.assertFalse((self.data / "runs" / "C" / "q1-1.jsonl").exists())
        self.assertTrue((self.data / "runs" / "C" / "q1-1.invalid").exists())
        self.assertEqual(self.calls.read_text(), "xx")
        self.assertIn("not counted, because the notes hook did not add the brief", out.getvalue())


class Verdicts(unittest.TestCase):

    def test_every_label_must_be_graded_exactly_once(self):
        self.assertEqual(cm.parse_verdicts("1 right fine\n2 wrong misses the reason\n", 2),
                         {1: "right", 2: "wrong"})
        # The form a real judge used on 2026-10-15, and two more it could use.
        self.assertEqual(cm.parse_verdicts("1. right ok\n\n**2** - Partly half\n3) wrong no\n", 3),
                         {1: "right", 2: "partly", 3: "wrong"})
        with self.assertRaises(SystemExit):
            cm.parse_verdicts("1 right fine\n", 2)
        with self.assertRaises(SystemExit):
            cm.parse_verdicts("1 right fine\n1 wrong no\n2 right fine\n", 2)


class Judge(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = pathlib.Path(tmp.name)
        (self.data / "snapshots" / "repo").mkdir(parents=True)
        (self.data / "questions.json").write_text(json.dumps([{
            "id": "q1", "repo": "repo", "kind": "why", "question": "Why?",
            "expected_files": ["x.py"], "answer_key": "Because."}]))
        bin_dir = self.data / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "claude"
        # Grades every answer it is shown as right, and counts its calls.
        fake.write_text(f"""#!{sys.executable}
import json, os, sys
open(os.environ["FAKE_CALLS"], "a").write("x")
shown = sys.argv[2].count('<answer label=')
print(json.dumps({{"result": "\\n".join(f"{{i}} right fine" for i in range(1, shown + 1))}}))
""")
        fake.chmod(0o755)
        self.calls = self.data / "calls"
        env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_CALLS": str(self.calls)}
        for patcher in (mock.patch.object(cm, "DATA", self.data), mock.patch.dict(os.environ, env)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def add_run(self, arm, n):
        path = self.data / "runs" / arm / f"q1-{n}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines(init(), result(text=f"answer {arm}{n}"))) + "\n")

    def test_a_question_is_graded_again_when_its_answers_change(self):
        self.add_run("A", 1)
        with redirect_stdout(io.StringIO()):
            cm.judge()
            self.add_run("C", 1)
            cm.judge()
            cm.judge()
        verdicts = json.loads((self.data / "judge" / "q1.json").read_text())["verdicts"]
        self.assertEqual(verdicts, {"A/q1-1": "right", "C/q1-1": "right"})
        self.assertEqual(self.calls.read_text(), "xx")
