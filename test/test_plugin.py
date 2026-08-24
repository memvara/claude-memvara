"""Gates for the Claude Code marketplace plugin.

Every file the client will read is asserted here. Markdown is not exempt:
a wrong URL or an npx block is how this repo goes wrong.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import re
import shutil
import tempfile
import subprocess
import sys
import unittest
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"
HOOKS = PLUGIN / "hooks"
HOSTED = "https://app.memvara.dev/mcp"
REPO_NAME = "memvara/claude-memvara"

#: The skill ships as `memory` so the client renders it `/memvara:memory` instead of
#: `/memvara:memvara`. The library still calls it `memvara`, which is the right name
#: everywhere else — including the bare `~/.claude/skills/memvara/` install, where there
#: is no plugin segment to pair it with. The gap is one frontmatter line, and
#: `test_matches_library_at_lock_sha` asserts it is the only one.
SKILL_NAME = "memory"
LIBRARY_SKILL_NAME = "memvara"
LIBRARY_SKILL_PATH = "memvara/skills/memvara"
SKILL = PLUGIN / "skills" / SKILL_NAME

#: Hook scripts are executable content the client runs on every prompt, so the allowlist
#: names them one by one. A file appearing under `hooks/` that nobody listed here is the
#: failure this gate exists to catch.
ALLOWED_PLUGIN_FILES = {
    pathlib.Path(".claude-plugin") / "plugin.json",
    pathlib.Path(".mcp.json"),
    pathlib.Path("hooks") / "hooks.json",
    pathlib.Path("hooks") / "recall.py",
    pathlib.Path("hooks") / "session_start.py",
    pathlib.Path("hooks") / "capture.py",
    pathlib.Path("hooks") / "lib" / "__init__.py",
    pathlib.Path("hooks") / "lib" / "open.py",
    pathlib.Path("hooks") / "lib" / "extract.py",
    pathlib.Path("hooks") / "lib" / "usage.py",
    pathlib.Path("hooks") / "daemon.py",
    pathlib.Path("hooks") / "lib" / "ipc.py",
    pathlib.Path("hooks") / "lib" / "fast.py",
    pathlib.Path("hooks") / "lib" / "hosted.py",
    pathlib.Path("hooks") / "approve.py",
    pathlib.Path("hooks") / "lib" / "transcript.py",
    pathlib.Path("hooks") / "lib" / "write.py",
}


def _json(path: pathlib.Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _library_bytes(sha: str, path: str) -> bytes:
    """Bytes of a library file at `sha`. Git first (offline), then GitHub."""
    root = os.environ.get("MEMVARA_LIBRARY")
    if root:
        return subprocess.check_output(
            ["git", "-C", root, "show", f"{sha}:{path}"],
        )
    url = f"https://raw.githubusercontent.com/memvara/memvara/{sha}/{path}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()


def _lock() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (ROOT / "skill.lock").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


class Marketplace(unittest.TestCase):
    def test_marketplace_lists_one_plugin_at_dot_plugin(self) -> None:
        body = _json(ROOT / ".claude-plugin" / "marketplace.json")
        assert isinstance(body, dict)
        plugins = body["plugins"]
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0]["name"], "memvara")
        self.assertEqual(plugins[0]["source"], "./plugin")
        self.assertEqual(plugins[0]["homepage"], "https://memvara.dev/docs/agents")

    def test_plugin_manifest(self) -> None:
        body = _json(PLUGIN / ".claude-plugin" / "plugin.json")
        assert isinstance(body, dict)
        self.assertEqual(body["name"], "memvara")
        self.assertEqual(body["version"], "0.1.5")
        self.assertEqual(body["license"], "Apache-2.0")
        self.assertEqual(body["homepage"], "https://memvara.dev/docs/agents")
        self.assertEqual(body["repository"], f"https://github.com/{REPO_NAME}")


class McpConfig(unittest.TestCase):
    def test_hosted_http_only(self) -> None:
        body = _json(PLUGIN / ".mcp.json")
        assert isinstance(body, dict)
        server = body["mcpServers"]["memvara"]
        self.assertEqual(server["url"], HOSTED)
        self.assertEqual(server.get("type"), "http")
        self.assertNotIn("command", server)
        self.assertNotIn("args", server)
        raw = (PLUGIN / ".mcp.json").read_text(encoding="utf-8")
        self.assertNotIn("npx", raw)
        self.assertNotIn("python3", raw)
        self.assertNotIn("stdio", raw)


class SkillTree(unittest.TestCase):
    def test_skill_has_front_matter_and_references(self) -> None:
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        named = set(re.findall(r"references/([a-z0-9-]+\.md)", text))
        self.assertTrue(named)
        for name in named:
            self.assertTrue((SKILL / "references" / name).is_file(), name)

    def test_every_reference_file_is_markdown(self) -> None:
        refs = list((SKILL / "references").glob("*.md"))
        self.assertGreaterEqual(len(refs), 7)

    def test_skill_is_named_for_the_plugin_segment(self) -> None:
        """`/memvara:memory`, not `/memvara:memvara`.

        The client builds the invocation from the plugin name and the skill's own
        frontmatter, so a directory rename alone changes nothing. Both are asserted
        because a mismatch between them is silent.
        """
        self.assertEqual(SKILL.name, SKILL_NAME)
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(text, rf"(?m)^name:[ \t]+{re.escape(SKILL_NAME)}[ \t]*$")

    def test_matches_library_at_lock_sha(self) -> None:
        """Vendored bytes equal the library's, except the one line we rename.

        Comparing after a targeted substitution rather than skipping the file keeps the
        drift gate at full strength: every other byte of the skill still has to match,
        and the rename itself fails loudly if the library's frontmatter ever stops
        saying what this expects.
        """
        lock = _lock()
        self.assertEqual(lock["repo"], "memvara/memvara")
        self.assertEqual(lock["path"], LIBRARY_SKILL_PATH)
        sha = lock["sha"]
        self.assertEqual(len(sha), 40)

        for rel in ("SKILL.md", "references/hosted-mcp.md"):
            expected = _library_bytes(sha, f"{LIBRARY_SKILL_PATH}/{rel}")
            if rel == "SKILL.md":
                old = f"name: {LIBRARY_SKILL_NAME}\n".encode()
                self.assertIn(old, expected, f"library frontmatter at {sha} is not {old!r}")
                expected = expected.replace(old, f"name: {SKILL_NAME}\n".encode(), 1)
            got = (SKILL / rel).read_bytes()
            self.assertEqual(got, expected, f"{rel} drifted from {sha}")


class Hooks(unittest.TestCase):
    """The hooks run unattended on every prompt, so the bar is behavioural.

    This file used to assert `hooks/` did not exist. That was the right call while the
    plugin was only an MCP endpoint and a skill: a hook is code the client executes
    without being asked, and shipping one is a different promise than shipping prose.
    The promise is kept here instead of avoided — every script is executed, against a
    deliberately empty environment, and required to stay silent and succeed.
    """

    #: Every hook must survive this: no store, no credentials, no library.
    BARREN = {"HOME": "/nonexistent", "PATH": os.environ.get("PATH", "")}

    def _scripts(self) -> list[str]:
        body = _json(HOOKS / "hooks.json")
        assert isinstance(body, dict)
        found = []
        for entries in body["hooks"].values():
            for entry in entries:
                for hook in entry["hooks"]:
                    self.assertEqual(hook["type"], "command")
                    found.append(hook["command"])
        return found

    def test_every_referenced_script_exists(self) -> None:
        for command in self._scripts():
            match = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/(\S+?)\"", command)
            self.assertIsNotNone(match, command)
            assert match is not None
            self.assertTrue((PLUGIN / match.group(1)).is_file(), command)

    def test_covers_the_events_that_make_memory_automatic(self) -> None:
        body = _json(HOOKS / "hooks.json")
        assert isinstance(body, dict)
        # Recall on every prompt is the whole point; capture is what keeps it fed.
        # PreToolUse is the SuperMemory-shaped auto-allow for read-only memory_* tools.
        self.assertLessEqual(
            {"UserPromptSubmit", "SessionStart", "Stop", "PreToolUse"},
            set(body["hooks"]),
        )

    def test_hooks_succeed_with_nothing_configured(self) -> None:
        """No store, no login, no transcript: exit 0 and never a traceback.

        Two of these now print, which is a change from the rule that a hook with nothing
        configured prints nothing. What that rule protects is the prompt, and a
        `systemMessage` cannot fail one: it is a line in the terminal. Printing it is how
        a hook that is working stops being indistinguishable from one that is not, which
        is the failure that cost the most time here. What must still hold is that the
        output is well-formed and the exit code is 0.
        """
        env = self.BARREN
        payload = json.dumps({"prompt": "hello", "transcript_path": "/nonexistent"})
        for script in ("recall.py", "session_start.py", "capture.py", "approve.py"):
            with self.subTest(script=script):
                proc = subprocess.run(
                    ["python3", str(HOOKS / script)],
                    input=payload, capture_output=True, text=True,
                    env=env, timeout=30,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                if proc.stdout.strip():
                    json.loads(proc.stdout)  # must be the JSON protocol, not loose text

    def test_recall_reports_itself_to_the_person_at_the_terminal(self) -> None:
        """Plain stdout on this event reaches the model and nobody else.

        The whole point of the JSON reply is `systemMessage`, which is the only field the
        user sees. A recall hook that silently stopped working looked exactly like one
        with nothing to say, for a whole session.
        """
        proc = subprocess.run(
            ["python3", str(HOOKS / "recall.py")],
            input=json.dumps({"prompt": "hello"}), capture_output=True, text=True,
            env=self.BARREN, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout)
        self.assertIn("Memvara", body["systemMessage"])
        # Nothing was configured, so there is no context to add -- but the report still
        # goes out. That asymmetry is the feature.
        self.assertNotIn("hookSpecificOutput", body)

    def test_readonly_memory_tools_are_allowed_without_a_prompt(self) -> None:
        payload = json.dumps({"tool_name": "mcp__memvara__memory_search"})
        proc = subprocess.run(
            ["python3", str(HOOKS / "approve.py")],
            input=payload, capture_output=True, text=True,
            env=self.BARREN, timeout=10,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout)
        self.assertEqual(body["hookSpecificOutput"]["permissionDecision"], "allow")

    def test_every_readonly_memory_tool_is_allowed_without_a_prompt(self) -> None:
        """The list must cover every read, not the reads that existed when it was written.

        `memory_neighborhood` and `memory_paths` were missing for exactly that reason. A
        read that prompts is a read the model learns not to make, so an incomplete
        allowlist quietly narrows what memory is used for.
        """
        for leaf in ("memory_recall", "memory_search", "memory_since", "memory_history",
                     "memory_why", "memory_stats", "memory_neighborhood", "memory_paths"):
            with self.subTest(tool=leaf):
                proc = subprocess.run(
                    ["python3", str(HOOKS / "approve.py")],
                    input=json.dumps({"tool_name": f"mcp__memvara__{leaf}"}),
                    capture_output=True, text=True, env=self.BARREN, timeout=10,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                body = json.loads(proc.stdout)
                self.assertEqual(
                    body["hookSpecificOutput"]["permissionDecision"], "allow")

    def test_recall_distinguishes_nothing_found_from_could_not_ask(self) -> None:
        """Three outcomes, three messages. Two of them used to read identically.

        A hosted client whose session had gone stale answered every query with silence for
        a whole session while the banner said "no matching notes" each time. From the
        terminal that is what an empty store looks like, and nobody investigates an empty
        store. The words have to differ or the failure stays invisible.
        """
        source = (HOOKS / "recall.py").read_text(encoding="utf-8")
        for phrase in ("recall failed", "no matching memories", "recalled"):
            self.assertIn(phrase, source)

    def test_capture_is_async_and_therefore_reports_to_the_log(self) -> None:
        """Async is why capture prints nothing, and the log is why that is still honest.

        Extraction takes 12-14s and a synchronous `Stop` hook holds the turn open for all
        of it. Async hands the turn straight back — but the client discards an async hook's
        output, so a `systemMessage` there is not merely unread, it is impossible.

        That reverses this repository's own rule that a hook must be visible, and the rule
        was right: a hook nobody can see working is one nobody notices breaking. So the
        obligation moved rather than lapsed. Both halves are asserted here, because either
        one alone is a defect — async with no log is a silent hook, and a log with no async
        is a turn held open for nothing.
        """
        stop = _json(HOOKS / "hooks.json")["hooks"]["Stop"][0]["hooks"][0]
        self.assertTrue(stop.get("async"), "capture must not hold the turn open")

        source = (HOOKS / "capture.py").read_text(encoding="utf-8")
        self.assertNotIn("emit_json", source,
                         "an async hook's output is discarded; printing implies otherwise")
        self.assertIn("log(", source, "the log is the only account left")

        # Every branch that reaches a decision must leave a trace. The guard clauses above
        # it return before anything happens and are legitimately silent.
        body = source[source.index("def main()"):source.index("def _keep_turn")]
        decisions = body[body.index("turn = _turn(transcript)"):]
        for fragment in ("no turn to mine", "skipped=", "no store or login", "facts=0"):
            self.assertIn(fragment, decisions, f"{fragment!r} must be logged")

    def test_nothing_injected_exceeds_the_clip(self) -> None:
        """Storage stays rich; injection does not. They are different jobs.

        Making procedural objects carry their reasoning is what stopped them being useless
        one-liners — and it made each about four times bigger. Measured over eight real
        prompts against a 222-claim store: median injected memory 48 tokens, p90 237, max
        503, and four lines over 150 tokens accounted for 39% of every token injected. The
        whole note is still stored, still embedded, still what memory_search returns.
        """
        recall = self._recall()
        long_line = "- " + "x" * 900
        clipped = recall._clip(long_line)
        self.assertLessEqual(len(clipped), recall.MAX_INJECTED_CHARS + 1)
        self.assertTrue(clipped.endswith("…"))
        short = "- already short"
        self.assertEqual(recall._clip(short), short, "a short memory is untouched")

    def test_the_block_says_when_it_is_showing_excerpts(self) -> None:
        """A pointer is what turns a truncated push into something the model can follow.

        Present only when something was actually shortened: promising that memory_search
        has more, on a block where it does not, is a lie the model cannot check.
        """
        recall = self._recall()
        source = (HOOKS / "recall.py").read_text(encoding="utf-8")
        self.assertIn("memory_search", recall.MORE)
        self.assertIn("if any(short != full", source,
                      "the pointer must be conditional on something being clipped")

    def test_dedup_hashes_the_memory_not_the_excerpt(self) -> None:
        """Otherwise raising the clip makes everything already in context look new."""
        source = (HOOKS / "recall.py").read_text(encoding="utf-8")
        write = source.index("_write_state(session, seen + [_digest(line) for line in fresh]")
        clip = source.index("clipped = [_clip(line) for line in fresh]")
        self.assertLess(write, clip, "hash the full line, then clip for display")

    def test_the_episode_pass_selects_wider_and_injects_no_wider(self) -> None:
        """Select generously, inject tersely. The first version had this backwards.

        `EPISODE_K` was set *below* `K` to "bound" the escalation, on the reasoning that an
        episode is the largest thing this hook can inject. But `k` is the candidate cap that
        episodes have to win a slot inside, and episodes are deliberately down-weighted
        against claims — so shrinking it guaranteed no episode could ever place. Measured
        against the deployed server, on a query whose answer is a stored turn:

            k \ budget    300    600   1200   2000
            k=2            -      -      -      -
            k=4            -   episode episode episode
            k=6            -      -      -   episode

        `k=2, budget=300` — what it shipped with — is the dead zone: it fired on most
        prompts and could not return an episode at any budget.

        What bounds the cost is the clip, not the candidate cap. So the selection pass is
        allowed to be wider than the claims pass, and every line it returns still goes
        through `_clip` — a 1,853-character median episode arrives as a 160-character
        pointer.
        """
        recall = self._recall()
        self.assertGreaterEqual(recall.EPISODE_K, recall.K,
                                "a narrower candidate cap means episodes never place")
        self.assertGreater(recall.EPISODE_BUDGET, recall.BUDGET,
                           "claims fill the budget first; episodes need room to survive")
        source = (HOOKS / "recall.py").read_text(encoding="utf-8")
        self.assertIn("k=EPISODE_K, budget=EPISODE_BUDGET", source)

    def test_the_escalation_trigger_is_calibrated_to_the_budget(self) -> None:
        """`THIN` counts memories, so it only means anything against a given budget.

        Two was set when the block returned six; at a 300-token budget returning one to
        three, "fewer than two" is the ordinary case. Measured over eight real prompts, the
        escalation went from firing on 2 of 8 to 5 of 8 — an extra round trip on most turns,
        bought entirely by tightening the budget it was tuned against.
        """
        recall = self._recall()
        self.assertLess(recall.THIN, recall.K,
                        "a trigger at or above K fires on every prompt")
        self.assertLessEqual(recall.THIN, 1,
                             "at BUDGET=300 the claims pass often returns one memory")

    def test_the_standing_set_is_asked_for_where_it_is_paid_for_once(self) -> None:
        """Procedural memories apply to every turn, so they belong in the opening block.

        Retrieved per prompt they are paid for again each time and crowd out the incidental
        facts that prompt was actually about. The split is the design; a regression that
        quietly asks for everything in both places has no symptom but the bill.
        """
        opening = (HOOKS / "session_start.py").read_text(encoding="utf-8")
        per_prompt = (HOOKS / "recall.py").read_text(encoding="utf-8")
        self.assertIn('STANDING = ["procedural"]', opening)
        self.assertIn("memory_types=STANDING", opening)
        self.assertNotIn("memory_types", per_prompt,
                         "the per-prompt hook must not re-retrieve the standing set")

    def test_the_read_path_is_metered(self) -> None:
        """The write path has had a token ledger since 0.1.2 and the read path had none.

        So the hook that spends context on every prompt was the one nobody could measure,
        which is how it came to spend four times what it needed to without anyone noticing.
        """
        source = (HOOKS / "recall.py").read_text(encoding="utf-8")
        self.assertIn('log_line("recall"', source)
        self.assertIn("injected=", source)
        # And it must not drag pathlib onto the per-prompt path to do it.
        self.assertNotIn("from pathlib import",
                         (HOOKS / "lib" / "ipc.py").read_text(encoding="utf-8"))

    def test_counts_read_as_english(self) -> None:
        """`1 memories stored from this turn` is what a machine writes, not a person.

        Shared between the three hooks rather than written out in each, because the bug is
        not getting it wrong once — it is getting it right in two places and forgetting the
        third.
        """
        sys.path.insert(0, str(HOOKS))
        try:
            from lib.ipc import plural
        finally:
            sys.path.pop(0)
        self.assertEqual(plural(0), "0 memories")
        self.assertEqual(plural(1), "1 memory")
        self.assertEqual(plural(2), "2 memories")
        # capture.py is absent on purpose: it runs async, the client discards an async
        # hook's output, and it therefore has no count to render for anyone.
        for hook in ("recall.py", "session_start.py"):
            source = (HOOKS / hook).read_text(encoding="utf-8")
            self.assertIn("plural(", source, f"{hook} must use the shared pluraliser")

    def test_an_unconfigured_install_is_not_reported_as_a_failure(self) -> None:
        """Four outcomes, four messages. "not configured" is the one added last.

        Splitting "asked and got nothing" from "could not ask" left a third case wearing the
        wrong label: an install with no database, no library and no credentials has nothing
        to ask, and calling that a failure sends someone who has simply not logged in to
        read a log that will tell them nothing. It is still *reported*, because a hook that
        prints nothing is indistinguishable from one that has stopped working — which is the
        failure this whole file exists to stop repeating.
        """
        proc = subprocess.run(
            ["python3", str(HOOKS / "recall.py")],
            input=json.dumps({"prompt": "hello there friend", "session_id": "s"}),
            capture_output=True, text=True, env=self.BARREN, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout)
        self.assertIn("not configured", body["systemMessage"])
        self.assertNotIn("failed", body["systemMessage"])
        self.assertNotIn("hookSpecificOutput", body)

        source = (HOOKS / "recall.py").read_text(encoding="utf-8")
        for phrase in ("not configured", "recall failed", "no matching memories",
                       "recalled"):
            self.assertIn(phrase, source, "each outcome needs its own words")

    def _recall(self):
        sys.path.insert(0, str(HOOKS))
        try:
            import importlib

            return importlib.import_module("recall")
        finally:
            sys.path.pop(0)

    def test_an_anaphoric_prompt_is_told_apart_from_a_real_one(self) -> None:
        """"yes please" has nothing in it to search on, and the query was the prompt.

        A vector search over two function words returns arbitrary neighbours: measured on a
        real store, a turn approving a memory cleanup was handed notes about pricing tiers
        and an unrelated project's zip layout. No error, no signal, the whole block's
        budget spent on noise.

        The two errors here are not symmetric, which is why the rule is tuned to guess
        towards substantive. Reading a terse prompt as substantive costs one weak query and
        self-corrects. Reading a real prompt as anaphoric freezes the carried topic, so
        every later turn searches something stale — it compounds. A first draft had "what"
        and "why" among the openers and did exactly that.
        """
        recall = self._recall()
        for prompt in ("yes please", "Yes", "yes, add that fix to #7",
                       "go ahead with all 8", "why?", "do it", "ok"):
            self.assertTrue(recall._anaphoric(prompt), f"{prompt!r} is a reply")
        for prompt in ("what does the user prefer for file path citation style",
                       "fix the daemon protocol", "run the tests",
                       "add a boolean branch to the validator"):
            self.assertFalse(recall._anaphoric(prompt), f"{prompt!r} is about something")

    def test_the_carried_topic_only_advances_on_a_substantive_prompt(self) -> None:
        """Three "yes" replies in a row all search the last prompt that had a topic in it.

        Driven through `main()`, not through the state helpers. A first version of this
        test round-tripped `_write_state`/`_read_state` and asserted nothing about the line
        that decides what gets written — so it passed with that line replaced by `topic =
        prompt`, which is the bug: one "yes" and the carried topic becomes "yes".
        """
        recall = self._recall()
        directory = tempfile.mkdtemp()
        original_dir, original_recall = recall.SEEN_DIR, recall.fast_recall
        recall.SEEN_DIR = directory
        asked = []

        def fake_recall(query, **kw):
            # Two bullets, not one: a single fresh memory is below THIN and triggers the
            # episode escalation, which makes a second call per turn and puts turn 0's
            # retry where the test expects turn 1's query.
            asked.append(query)
            return f"{recall.HEADER}\n- memory {len(asked)}a\n- memory {len(asked)}b", True

        recall.fast_recall = fake_recall
        try:
            for prompt in ("the file path citation style across repos", "yes please",
                           "yes", "go ahead"):
                with contextlib.redirect_stdout(io.StringIO()):
                    recall.main.__globals__["payload"] = lambda p=prompt: {
                        "prompt": p, "session_id": "s"}
                    recall.main()

            _, carried = recall._read_state("s")
            self.assertEqual(carried, "the file path citation style across repos",
                             "three replies in a row must not move the topic")
            self.assertEqual(len(asked), 4, "one recall per turn, no escalation")
            self.assertEqual(asked[0], "the file path citation style across repos")
            self.assertEqual(asked[1:], [f"{carried} yes please", f"{carried} yes",
                                         f"{carried} go ahead"],
                             "each reply searches the carried topic AND its own words")
        finally:
            recall.SEEN_DIR = original_dir
            recall.fast_recall = original_recall
            shutil.rmtree(directory)

    def test_session_state_survives_junk_and_the_format_it_used_to_have(self) -> None:
        """An upgrade mid-session costs the carried query, never the dedup."""
        recall = self._recall()
        directory = tempfile.mkdtemp()
        original = recall.SEEN_DIR
        recall.SEEN_DIR = directory
        try:
            recall._write_state("s", ["h1"], "the file path citation style")
            self.assertEqual(recall._read_state("s"),
                             (["h1"], "the file path citation style"))

            # A bare list is the format this file used before it carried a query.
            with open(os.path.join(directory, "old.json"), "w", encoding="utf-8") as fh:
                json.dump(["h2"], fh)
            self.assertEqual(recall._read_state("old"), (["h2"], ""))

            for junk in ("not json", "{}", '{"seen": "no", "query": 5}', "[1, 2]"):
                with open(os.path.join(directory, "j.json"), "w", encoding="utf-8") as fh:
                    fh.write(junk)
                self.assertEqual(recall._read_state("j"), ([], ""), junk)
        finally:
            recall.SEEN_DIR = original
            shutil.rmtree(directory)

    def test_a_memory_already_injected_is_not_injected_again(self) -> None:
        """Turn 1 puts it in context; turn 5 must not put it there a second time.

        The block stays in the conversation once injected, so repeating it spends budget a
        genuinely new memory could have had, and makes the banner report the same number
        every turn whether or not anything was learned.
        """
        sys.path.insert(0, str(HOOKS))
        try:
            import importlib

            recall = importlib.import_module("recall")
        finally:
            sys.path.pop(0)

        block = (recall.HEADER + "\n- user prefers absolute paths\n- user lives in Delhi")
        header, bullets = recall._split(block)
        self.assertEqual(header, recall.HEADER)
        self.assertEqual(len(bullets), 2, "only `- ` lines are memories")

        seen = {recall._digest(bullets[0])}
        fresh = [b for b in bullets if recall._digest(b) not in seen]
        self.assertEqual(fresh, [bullets[1]])

        # Whitespace must not make one memory look like two.
        self.assertEqual(recall._digest("- a   b"), recall._digest("- a b"))

    def test_per_prompt_recall_asks_for_claims_before_episodes(self) -> None:
        """Claims first. Episodes only when the structured layer came back thin.

        `include_episodes` is off by default in the core because a claim is a settled
        reading of what was said and an excerpt is not — mixing them lets something the
        user once said outrank something known to be true. A regression here inverts that
        ranking and has no other symptom.
        """
        source = (HOOKS / "recall.py").read_text(encoding="utf-8")
        first = source.index("block, ok = fast_recall(")
        escalation = source.index("include_episodes=True")
        self.assertLess(first, escalation, "the plain call must come first")
        self.assertIn("if len(fresh) < THIN:", source,
                      "episodes are conditional, not the default")

    def test_session_start_works_without_a_local_store(self) -> None:
        """The hosted install is the default one, and this hook never ran on it.

        It opened the store with `open_store()`, which answers None when there is no local
        database and no library to read one with — the normal state on a paste-the-URL
        install. Recall and capture both grew a hosted fallback and this one did not, so
        the hook that exists to open a session already knowing the user had never once
        produced output for most people who installed it.

        Driven through `main()` with a stubbed backend rather than grepped for. The grep
        version of this test survived the fallback being removed, because the name it
        looked for still appeared at the call site while the import beside it was wrong.
        """
        sys.path.insert(0, str(HOOKS))
        try:
            import importlib

            session_start = importlib.import_module("session_start")
        finally:
            sys.path.pop(0)

        class HostedOnly:
            """No `.scope`, no `.count` — exactly what the stdlib client offers."""

            def stats(self):
                return ("scope: prj_x/*/*/*  (tenant/user/agent/session)\n"
                        "writes: enabled\nvisible at this scope: 204 claim(s)")

            def recall(self, query, **kw):
                # The standing set is a separate, narrower request. Answering the two the
                # same way would let the split be removed without this test noticing.
                if kw.get("memory_types") == ["procedural"]:
                    return "STANDING:\n- user always opens a PR"
                return "HEAD:\n- user prefers tabs"

        original = session_start.open_writer
        session_start.open_writer = lambda: (HostedOnly(), lambda: None)
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                code = session_start.main()
        finally:
            session_start.open_writer = original

        self.assertEqual(code, 0)
        body = json.loads(buffer.getvalue())
        context = body["hookSpecificOutput"]["additionalContext"]
        self.assertIn("prj_x/*/*/*", context, "the binding must survive the hosted route")
        self.assertIn("204 claim(s)", context)
        self.assertIn("user prefers tabs", context, "and so must the memories")
        self.assertIn("user always opens a PR", context,
                      "the standing procedural set is asked for separately, and here")
        self.assertIn("2 memories", body["systemMessage"])

    def test_writes_are_not_auto_approved(self) -> None:
        payload = json.dumps({"tool_name": "mcp__memvara__memory_forget"})
        proc = subprocess.run(
            ["python3", str(HOOKS / "approve.py")],
            input=payload, capture_output=True, text=True,
            env=self.BARREN, timeout=10,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_transcript_mines_edits_and_drops_recall_injection(self) -> None:
        sys.path.insert(0, str(HOOKS))
        try:
            from lib.transcript import span_from_bytes
        finally:
            sys.path.pop(0)
        raw = "\n".join([
            json.dumps({"type": "user", "message": {"content": "where is the store"}}),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "thinking", "thinking": "do not store this"},
                {"type": "text", "text": "Putting it in plugin/"},
                {"type": "tool_use", "name": "Edit",
                 "input": {"file_path": "plugin/hooks/open.py"}},
            ]}}),
            json.dumps({"type": "user", "message": {
                "content": "Recalled from Memvara\n- a standing fact"}}),
        ]).encode()
        span = span_from_bytes(raw)
        self.assertIn("User: where is the store", span)
        self.assertIn("Claude: Putting it in plugin/", span)
        self.assertIn("Claude used Edit", span)
        self.assertNotIn("do not store this", span)
        self.assertNotIn("Recalled from Memvara", span)

    def test_extraction_cannot_recurse(self) -> None:
        """A Stop hook that spawns Claude must not give the child a Stop hook.

        This is the failure that does not announce itself: the child inherits the same
        hook set, finishes, fires Stop, spawns another child. Nothing errors — the machine
        just fills with Claude processes and the bill climbs. Two independent stops are
        asserted because either one alone is a single point of failure.
        """
        source = (HOOKS / "lib" / "extract.py").read_text(encoding="utf-8")
        # 1. The child is launched with an empty hook set.
        self.assertIn('"--settings", \'{"hooks":{}}\'', source)
        # 2. And refuses to start if it finds itself already inside an extraction.
        self.assertIn("SENTINEL", source)

        sys.path.insert(0, str(HOOKS))
        try:
            from lib.extract import SENTINEL, _payload
        finally:
            sys.path.pop(0)

        original = os.environ.get(SENTINEL)
        os.environ[SENTINEL] = "1"
        try:
            result, usage = _payload("anything at all", "prompt")
            self.assertEqual(result, "", "extraction ran despite the recursion sentinel")
            self.assertEqual(usage, {}, "a blocked run must report no tokens spent")
        finally:
            if original is None:
                os.environ.pop(SENTINEL, None)
            else:
                os.environ[SENTINEL] = original

    def test_capture_mines_one_turn_and_never_skips_text(self) -> None:
        """Batching was cheaper and lost data, which is not a trade worth making.

        The old hook held text back until 2000 characters had accumulated, mined the last
        48 formatted lines of it, and moved its watermark past *everything* it had read.
        On a session with large tool outputs that skipped most of the transcript unread,
        permanently: 630KB consumed and six extractions paid for, with only the tail of
        each batch ever seen. Per turn costs more and drops nothing.
        """
        source = (HOOKS / "capture.py").read_text(encoding="utf-8")
        self.assertNotIn("MIN_SPAN_CHARS", source)
        self.assertIn("last_turn", source)

    def test_last_turn_is_the_prompt_and_its_reply(self) -> None:
        """Both halves, and only this turn's.

        The prompt carries the standing instruction and the reply carries what was
        decided; mining either alone loses one of them. Mining the reply by itself was
        tried and returned an empty list on every turn of a session, because facts about
        the user are not in Claude's own words.

        The boundary is deliberately not "the last entry of type user": tool results are
        user entries too, and that boundary cuts the turn in half.
        """
        sys.path.insert(0, str(HOOKS))
        try:
            from lib.transcript import last_turn
        finally:
            sys.path.pop(0)
        entries = [
            {"type": "user", "message": {"role": "user", "content": "first question"}},
            {"type": "assistant", "message": {"role": "assistant",
                                              "content": [{"type": "text", "text": "old answer"}]}},
            {"type": "user", "message": {"role": "user", "content": "always open a PR"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "git push"}}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "name": "Bash", "content": "pushed"}]}},
            {"type": "assistant", "message": {"role": "assistant",
                                              "content": [{"type": "text", "text": "opened #4"}]}},
        ]
        raw = "\n".join(json.dumps(e) for e in entries).encode("utf-8")
        turn = last_turn(raw)
        self.assertIn("User: always open a PR", turn, "the instruction lives in the prompt")
        self.assertIn("Claude: opened #4", turn, "what was decided lives in the reply")
        self.assertIn("Claude used Bash", turn)
        self.assertNotIn("old answer", turn, "the previous turn was mined when it happened")
        self.assertNotIn("first question", turn)

    def test_hooks_do_not_hardcode_a_store_path(self) -> None:
        # Configuration is discovered from the client's own server block. A literal path
        # here would silently read a different store than the MCP server writes.
        for path in HOOKS.rglob("*.py"):
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("MEMVARA_DB=", raw, path)
            self.assertNotIn("/.memvara/workstation", raw, path)


class Extraction(unittest.TestCase):
    """What a captured memory is allowed to be.

    Two defects met here, and both produced rows that looked fine in a browser. The model
    was asked to invent a predicate per fact, and an invented predicate is never registered,
    and an unregistered predicate is multi-valued forever -- so nothing it wrote could ever
    supersede anything, and one preference about file paths ended up occupying four live
    claims under four names. And the object was specified as "the value alone", which is
    right for `lives_in` and useless for a preference, where the value IS the instruction:
    the store still holds `deployment_approach = verification_first`, which no later session
    can act on.
    """

    def _extract(self):
        sys.path.insert(0, str(HOOKS))
        try:
            import importlib

            return importlib.import_module("lib.extract")
        finally:
            sys.path.pop(0)

    def _facts(self, extract, reply: str):
        original = extract._payload
        extract._payload = lambda text, prompt: (reply, {})
        try:
            return extract.triples("irrelevant")
        finally:
            extract._payload = original

    def test_an_invented_predicate_is_dropped(self) -> None:
        extract = self._extract()
        facts = self._facts(extract, json.dumps({"facts": [
            {"subject": "user", "predicate": "deployment_approach",
             "object": "verification first, always"},
        ]}))
        self.assertEqual(facts, [], "an unregistered predicate supersedes nothing")

    def test_a_valueless_object_is_dropped(self) -> None:
        """`user wants hooks printed to terminal = "true"` is a fact with its value missing.

        It answers nothing, and it still costs a slot and a line of recall budget.

        Tested against `lives_in` rather than `prefers` on purpose. `prefers` also carries a
        minimum length, and a mutation run showed that floor catching every one of these
        four characters before the emptiness rule was reached -- so the test passed with
        the rule it names deleted. A terse predicate leaves only the rule under test.
        """
        extract = self._extract()
        for value in ("true", "yes", "done", "n/a", "-", ""):
            with self.subTest(object=value):
                facts = self._facts(extract, json.dumps({"facts": [
                    {"subject": "user", "predicate": "lives_in", "object": value},
                ]}))
                self.assertEqual(facts, [], f"{value!r} is not a place to live")
        kept = self._facts(extract, json.dumps({"facts": [
            {"subject": "user", "predicate": "lives_in", "object": "Delhi"}]}))
        self.assertEqual(len(kept), 1, "the filter must not reject real short values")

    def test_a_procedural_memory_must_carry_its_own_context(self) -> None:
        """A later session cannot see this conversation, so the object has to stand alone.

        Dropping a thin one is right rather than harsh: the preference will be stated again,
        and a label sitting in the slot makes the good version look like a duplicate when it
        finally arrives.
        """
        extract = self._extract()
        thin = self._facts(extract, json.dumps({"facts": [
            {"subject": "user", "predicate": "prefers", "object": "absolute paths"},
        ]}))
        self.assertEqual(thin, [])

        rich_object = (
            "always cite files by full absolute path, never a relative one, because work "
            "spans three sibling repositories plus worktrees under each and a relative "
            "path names a file in two trees at once"
        )
        kept = self._facts(extract, json.dumps({"facts": [
            {"subject": "user", "predicate": "prefers", "object": rich_object},
        ]}))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].memory_type, "procedural",
                         "a standing instruction filed as `semantic` is invisible to the "
                         "`procedural` filter, which is the filter that looks for it")

    def test_a_terse_predicate_keeps_its_short_value(self) -> None:
        """The length floor applies to `prefers`, not to `lives_in`."""
        extract = self._extract()
        facts = self._facts(extract, json.dumps({"facts": [
            {"subject": "user", "predicate": "lives_in", "object": "New Delhi"},
        ]}))
        self.assertEqual([(f.predicate, f.object) for f in facts],
                         [("lives_in", "New Delhi")])

    def test_a_project_fact_never_takes_the_user_subject(self) -> None:
        """One subject for everything is how a store ends up with a 1% join rate."""
        extract = self._extract()
        facts = self._facts(extract, json.dumps({"facts": [
            {"subject": "user", "predicate": "known_defect",
             "object": "the hosted validator has no boolean branch, so include_episodes "
                       "raises KeyError for either value"},
        ]}))
        self.assertEqual(len(facts), 1)
        self.assertNotEqual(facts[0].subject, "user")

    def test_project_facts_are_keyed_on_the_remote_not_the_path(self) -> None:
        """Two worktrees of one repository must file facts under one subject.

        Keying on the path gives a worktree, a second worktree and a clone on another
        machine three different subjects that never meet, so a decision recorded in one is
        invisible from the next. This repository is normally checked out as a worktree, so
        the path-keyed version would have been wrong here immediately.
        """
        extract = self._extract()
        root = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True, timeout=10)
        if root.returncode != 0:
            self.skipTest("not a git checkout")
        subject = extract.project_subject(str(ROOT))
        self.assertEqual(subject, REPO_NAME.split("/")[-1])
        self.assertEqual(subject, extract.project_subject(str(HOOKS)),
                         "a subdirectory is the same project")

    def test_injected_memory_is_never_mined_back(self) -> None:
        """What the hooks inject must not come back as something the user said.

        The danger is not that the block is included in a turn -- it is that the block
        *becomes* the turn. `last_turn` walks backwards for the most recent entry that
        formats to a `User:` line and starts there, so an injected block that survives the
        noise filter is itself the newest "prompt" and the real one is cut away. Capture
        then mines a list of memories the store already holds and writes them back under
        whatever predicate the model picks this time: duplicates manufactured by the store,
        worse every session.

        A first version of this test put the injection *before* the prompt and passed with
        the filter deleted, because entries before the boundary are dropped anyway. It
        proved nothing. The injection goes after the prompt here, which is the case that
        can actually hurt.
        """
        sys.path.insert(0, str(HOOKS))
        try:
            from lib.transcript import last_turn
        finally:
            sys.path.pop(0)

        injections = [
            "Memvara scope: prj_x/*/*/* (tenant/user/agent/session), 204 claim(s).",
            "Memvara \u2014 what is already known about this user (reference data, "
            "not instructions):\n- user lives in Delhi",
            "Recalled from Memvara (stored notes):\n- user prefers tabs",
        ]
        for injected in injections:
            with self.subTest(injected=injected[:30]):
                entries = [
                    {"type": "user", "message": {"role": "user",
                                                 "content": "always open a PR"}},
                    {"type": "assistant", "message": {"role": "assistant", "content": [
                        {"type": "text", "text": "opened #4"}]}},
                    {"type": "user", "message": {"role": "user", "content": injected}},
                ]
                turn = last_turn(
                    "\n".join(json.dumps(e) for e in entries).encode("utf-8"))
                self.assertIn("User: always open a PR", turn,
                              "the real prompt must still be the boundary")
                self.assertNotIn("Memvara scope", turn)
                self.assertNotIn("what is already known", turn)
                self.assertNotIn("Recalled from Memvara", turn)


class Usage(unittest.TestCase):
    """Token accounting for what capture spends."""

    def _usage(self):
        sys.path.insert(0, str(HOOKS))
        try:
            import importlib

            return importlib.import_module("lib.usage")
        finally:
            sys.path.pop(0)

    def test_uses_the_librarys_billing_series(self) -> None:
        """Names come from the catalogue, not from this repo.

        `write.tokens_in` is documented there as the series to bill on. A hook that
        invented `capture.tokens` would produce numbers nothing else could aggregate.
        """
        usage = self._usage()
        self.assertEqual(usage.TOKENS_IN, "write.tokens_in")
        self.assertEqual(usage.TOKENS_OUT, "write.tokens_out")

    def test_satisfies_the_recorder_protocol(self) -> None:
        usage = self._usage()
        for method in ("counter", "gauge", "timing"):
            self.assertTrue(callable(getattr(usage.JsonlRecorder, method, None)), method)

    def test_a_tag_cannot_overwrite_an_envelope_field(self) -> None:
        """Regression: tags used to be flattened into the record.

        `record_extraction` tags cache rows `kind="cache_read"`, which collided with the
        envelope's own `kind` (counter/gauge/timing) and silently replaced it. The row
        stayed valid JSON, so nothing failed and the metric type was simply lost.
        """
        usage = self._usage()
        import tempfile

        path = pathlib.Path(tempfile.mkdtemp()) / "usage.jsonl"
        usage.JsonlRecorder(path).counter("write.tokens_in", 5, kind="cache_read")
        record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(record["kind"], "counter")
        self.assertEqual(record["tags"]["kind"], "cache_read")

    def test_input_total_includes_cache_reads_and_writes(self) -> None:
        """The cached preamble *is* the cost of a headless run.

        A measured extraction was 9 fresh input tokens against 21,130 cached. Counting
        only `input_tokens` would report the run as ~1/2000th of its true size and make
        the batching threshold look like premature optimisation.
        """
        usage = self._usage()
        import tempfile

        path = pathlib.Path(tempfile.mkdtemp()) / "usage.jsonl"
        usage.record_extraction(
            {"input_tokens": 9, "cache_read_input_tokens": 12206,
             "cache_creation_input_tokens": 8924, "output_tokens": 871},
            model="m", recorder=usage.JsonlRecorder(path))
        totals = usage.totals(path)
        self.assertEqual(totals["write.tokens_in"], 9 + 12206 + 8924)
        self.assertEqual(totals["write.tokens_out"], 871)

    def test_usage_is_not_written_into_the_memory_store(self) -> None:
        # Operational accounting belongs beside the store, not in it: "we spent 4,897
        # tokens" must never surface as a fact about the user in a recall block.
        source = (HOOKS / "lib" / "usage.py").read_text(encoding="utf-8")
        self.assertNotIn("remember(", source)
        self.assertNotIn("store.add", source)


class Daemon(unittest.TestCase):
    """The resident recall process. Optional by construction, private by permission."""

    def _ipc(self):
        sys.path.insert(0, str(HOOKS))
        try:
            import importlib

            return importlib.import_module("lib.ipc")
        finally:
            sys.path.pop(0)

    def test_socket_address_separates_stores(self) -> None:
        """Two stores must never share a daemon.

        A warm handle answers out of one specific database. Reached by a client configured
        for another, it would return one store's memories in another store's session --
        a privacy failure that looks exactly like a cache hit.
        """
        ipc = self._ipc()
        a = ipc.socket_path("store-a")
        b = ipc.socket_path("store-b")
        self.assertNotEqual(a, b)

    def test_socket_address_changes_when_hook_code_changes(self) -> None:
        """Edited code must strand the old daemon rather than be served by it.

        A long-lived process keeps running whatever it started with, and nothing about
        that looks wrong from outside. Folding the source digest into the address means a
        changed hook simply talks to a different socket.
        """
        ipc = self._ipc()
        import tempfile

        root = pathlib.Path(tempfile.mkdtemp())
        (root / "lib").mkdir()
        for name in ipc.CODE_FILES:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("original", encoding="utf-8")
        before = ipc.socket_path("k", root=str(root))
        (root / "recall.py").write_text("edited", encoding="utf-8")
        self.assertNotEqual(before, ipc.socket_path("k", root=str(root)))

    def test_missing_daemon_is_not_an_error(self) -> None:
        """`send` collapses every failure to None so the caller falls back.

        Refused connection, stale socket file, hung server, truncated reply: from the
        client's side these are one condition, and the response to all of them is to
        query in-process.
        """
        ipc = self._ipc()
        self.assertIsNone(ipc.send("/nonexistent/nowhere.sock", {"q": "x"}, timeout=0.5))

    def _daemon(self):
        sys.path.insert(0, str(HOOKS))
        try:
            import importlib

            return importlib.import_module("daemon")
        finally:
            sys.path.pop(0)

    def _fast(self):
        sys.path.insert(0, str(HOOKS))
        try:
            import importlib

            return importlib.import_module("lib.fast")
        finally:
            sys.path.pop(0)

    def test_a_failed_query_is_not_an_empty_answer(self) -> None:
        """The two used to be one value, and that is what hid a dead client for a session.

        Every exception collapsed to `""`, which is also what a store with nothing relevant
        returns. The client treated it as authoritative and never fell through, so one
        broken backend behind a live socket disabled recall entirely -- while the fallback
        chain that exists for exactly this sat unused.
        """
        daemon = self._daemon()

        class Broken:
            def recall(self, *a, **k):
                raise RuntimeError("backend is gone")

        class Empty:
            def recall(self, *a, **k):
                return ""

        broken = daemon.Daemon("/tmp/unused-broken.sock", Broken())
        self.assertEqual(broken._answer({"q": "hi"}), {"ok": False})

        empty = daemon.Daemon("/tmp/unused-empty.sock", Empty())
        self.assertEqual(empty._answer({"q": "hi"}), {"ok": True, "text": ""})

    def test_a_daemon_that_keeps_failing_gives_up(self) -> None:
        """A permanently broken backend must hand the address back, not hold it.

        Holding it means every prompt for the rest of the idle timeout pays a round trip to
        learn nothing. Exiting lets the next client take the fallback route and spawn a
        replacement that opens a fresh backend.
        """
        daemon = self._daemon()

        class Broken:
            def recall(self, *a, **k):
                raise RuntimeError("gone")

        d = daemon.Daemon("/tmp/unused.sock", Broken())
        for _ in range(daemon.MAX_CONSECUTIVE_FAILURES):
            d._answer({"q": "hi"})
        self.assertGreaterEqual(d.failures, daemon.MAX_CONSECUTIVE_FAILURES)

    def test_recall_falls_through_when_the_daemon_reports_failure(self) -> None:
        """`ok: false` must not end the search; `ok: true` with no text must."""
        fast = self._fast()
        self.assertIsNone(fast._served(None), "no daemon at all")
        self.assertIsNone(fast._served(json.dumps({"ok": False})), "daemon failed")
        self.assertIsNone(fast._served("not json at all"), "unreadable reply")
        self.assertEqual(fast._served(json.dumps({"ok": True, "text": ""})), "",
                         "a healthy daemon with nothing relevant is authoritative")
        self.assertEqual(
            fast._served(json.dumps({"ok": True, "text": "- a memory"})), "- a memory")

    def test_the_daemon_reads_every_argument_the_client_sends(self) -> None:
        """A dropped argument makes the two routes disagree, silently and only sometimes.

        `include_episodes` was sent by the client and never read here, so the daemon route
        answered a request for claims-plus-episodes with claims only -- and reported success,
        because from the client's side a well-formed `ok: true` reply is authoritative. The
        in-process route honoured it. Same call, two answers, no error on either side, and
        the difference appears only once a daemon happens to be up.

        The invariant is that every route returns the same text and only latency differs.
        Comparing the two sets of argument names is the cheapest thing that enforces it.
        """
        client = (HOOKS / "lib" / "fast.py").read_text(encoding="utf-8")
        served = (HOOKS / "daemon.py").read_text(encoding="utf-8")

        sent = set(re.findall(r'request\["(\w+)"\]\s*=', client))
        literal = re.search(r"request = \{([^}]*)\}", client)
        if literal:
            sent |= set(re.findall(r'"(\w+)":', literal.group(1)))
        read = set(re.findall(r'request\.get\("(\w+)"\)', served))

        self.assertTrue(sent, "no request keys found — the parse is wrong, not the code")
        self.assertEqual(sent - read, set(),
                         f"the client sends {sorted(sent - read)} and the daemon never "
                         "reads it, so the daemon route silently answers a different "
                         "question from the in-process one")

    def test_runtime_directory_is_private(self) -> None:
        # The socket is a read interface to everything the user has ever stored. The
        # default umask would leave it readable by every account on the machine.
        ipc = self._ipc()
        self.assertEqual(oct(os.stat(ipc.runtime_dir()).st_mode & 0o777), oct(0o700))

    def test_daemon_never_writes(self) -> None:
        source = (HOOKS / "daemon.py").read_text(encoding="utf-8")
        for call in (".remember(", ".add(", ".forget(", ".end("):
            self.assertNotIn(call, source, f"daemon must not {call}")

    def test_fast_path_does_not_import_pathlib(self) -> None:
        """pathlib costs 10.5ms measured, against a ~35ms client budget.

        `open.py` may use it freely -- it is only reached on the fallback path, where the
        cost is already lost in a 148ms in-process query.
        """
        for name in ("lib/ipc.py", "lib/fast.py", "recall.py", "lib/hosted.py"):
            source = (HOOKS / name).read_text(encoding="utf-8")
            self.assertNotIn("from pathlib import", source, name)


class Hosted(unittest.TestCase):
    """The stdlib-only path, so a hosted install needs no pip install."""

    def _hosted(self):
        sys.path.insert(0, str(HOOKS))
        try:
            import importlib

            return importlib.import_module("lib.hosted")
        finally:
            sys.path.pop(0)

    def test_a_stale_session_recovers(self) -> None:
        """A session id the server has forgotten must not kill the client permanently.

        `_rpc` returned None on any non-200 and left `_session` set, and `_ensure_session`
        short-circuits on a truthy value — so once the id went stale, nothing ever shook
        hands again and every call failed for the life of the object. Behind a resident
        daemon that is thirty minutes of a session with no memory at all, which is exactly
        what was measured before this test existed.
        """
        hosted = self._hosted()

        class Response:
            def __init__(self, status, body, session=None):
                self.status, self._body, self._session = status, body, session

            def getheader(self, _name):
                return self._session

            def read(self):
                return self._body.encode("utf-8")

        ok = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": "- a memory"}]}})
        # initialize, notifications/initialized, tools/call(STALE),
        # then the re-handshake and the retry.
        script = [
            Response(200, ok, "session-one"),
            Response(200, ok),
            Response(404, "session not found"),
            Response(200, ok, "session-two"),
            Response(200, ok),
            Response(200, ok),
        ]
        sent = []

        class Conn:
            def request(self, _method, _path, body, headers):
                sent.append(headers.get("mcp-session-id"))
                self._next = script.pop(0)

            def getresponse(self):
                return self._next

            def close(self):
                pass

        client = hosted.HostedRecall("key")
        client._connect = lambda: Conn()
        text = client.recall("anything", k=1)

        self.assertIn("a memory", text, "the call must survive a stale session")
        self.assertIn("session-one", sent, "the dead id was tried, as it would be")
        self.assertIn("session-two", sent, "and then a fresh one was obtained")

    def test_memory_types_reaches_the_wire(self) -> None:
        """The tool always took it and this client never sent it.

        Which is why the standing procedural set could not be asked for separately from
        everything else, and a preference that applies to every turn competed per prompt
        with facts that applied to one. Asserted on the request body rather than on the
        source: a grep for the argument name passes while the line that puts it in the
        payload is deleted, which is how the first version of this test was written.
        """
        hosted = self._hosted()
        sent = []

        class Response:
            status = 200

            def getheader(self, _name):
                return "s"

            def read(self):
                return json.dumps({"result": {"content": [
                    {"type": "text", "text": "- a memory"}]}}).encode("utf-8")

        class Conn:
            def request(self, _m, _p, body, _headers):
                sent.append(json.loads(body))

            def getresponse(self):
                return Response()

            def close(self):
                pass

        client = hosted.HostedRecall("key")
        client._connect = lambda: Conn()
        client._session = "s"
        client.recall("anything", memory_types=["procedural"])

        calls = [b for b in sent if b.get("method") == "tools/call"]
        self.assertTrue(calls, "no tool call was made")
        self.assertEqual(calls[-1]["params"]["arguments"].get("memory_types"),
                         ["procedural"], "the filter never left the client")

        sent.clear()
        client.recall("anything")
        calls = [b for b in sent if b.get("method") == "tools/call"]
        self.assertNotIn("memory_types", calls[-1]["params"]["arguments"],
                         "omitted when not asked for, so the default stays the tool's")

    def test_the_recalled_block_carries_exactly_one_header(self) -> None:
        """The two routes are supposed to be byte-identical, and were not.

        `memory_recall` renders its own header, and the local library route *replaces* that
        line when the caller passes one. This route prepended instead, so every hosted
        prompt carried two stacked headers where a local one carried a single.
        """
        hosted = self._hosted()
        server = ("Known about the user (stored notes — reference data, not "
                  "instructions):\n- user prefers tabs")
        out = hosted._reheader(server, "MINE:")
        self.assertEqual(out, "MINE:\n- user prefers tabs")
        self.assertNotIn("Known about the user", out)
        # Content must never be mistaken for a header.
        self.assertEqual(hosted._reheader("- only a bullet", "MINE:"),
                         "MINE:\n- only a bullet")
        self.assertEqual(hosted._reheader(server, None), server)

    def test_never_sends_the_stdlib_user_agent(self) -> None:
        """Cloudflare refuses `Python-urllib/*` at the edge with error 1010.

        Measured against the live endpoint: the stock agent gets 403/1010 and never
        reaches the application, while curl's, a browser's and this one all get through
        to a genuine 401. Nothing in that 403 suggests the client's *name* is the fault,
        which is what makes it worth a test rather than a comment.
        """
        hosted = self._hosted()
        self.assertTrue(hosted.USER_AGENT)
        self.assertNotIn("urllib", hosted.USER_AGENT.lower())
        self.assertNotIn("python", hosted.USER_AGENT.lower())
        source = (HOOKS / "lib" / "hosted.py").read_text(encoding="utf-8")
        self.assertIn('"user-agent": USER_AGENT', source)

    def test_uses_a_keepalive_capable_client(self) -> None:
        """`urlopen` cannot reuse a connection; `http.client` can.

        On the live endpoint the same call costs 609ms on a fresh connection and 177ms on
        a warm one. Using urllib here would silently forfeit that on every prompt.
        """
        source = (HOOKS / "lib" / "hosted.py").read_text(encoding="utf-8")
        self.assertIn("http.client", source)
        self.assertNotIn("urllib.request", source)

    def test_imports_nothing_outside_the_standard_library(self) -> None:
        # The entire point: a hosted install pastes a URL and gets working hooks. An
        # import of `memvara` here would make that false on exactly the target machine.
        source = (HOOKS / "lib" / "hosted.py").read_text(encoding="utf-8")
        self.assertNotIn("import memvara", source)
        self.assertNotIn("import httpx", source)

    def test_tolerates_a_missing_ca_bundle(self) -> None:
        """python.org's macOS build does not use the system trust store.

        Without a bundle it raises CERTIFICATE_VERIFY_FAILED against a certificate every
        other tool on the machine accepts, so `certifi` is preferred and the default
        context is the fallback rather than the only option.
        """
        source = (HOOKS / "lib" / "hosted.py").read_text(encoding="utf-8")
        self.assertIn("certifi", source)
        self.assertIn("ssl.create_default_context()", source)
        self.assertIsNotNone(self._hosted()._context())

    def test_no_credentials_is_not_an_error(self) -> None:
        hosted = self._hosted()
        original = hosted.CREDENTIALS
        hosted.CREDENTIALS = "/nonexistent/credentials.json"
        try:
            self.assertIsNone(hosted.credentials())
            self.assertIsNone(hosted.open_hosted())
        finally:
            hosted.CREDENTIALS = original


class ReadmeAndLicense(unittest.TestCase):
    def test_readme_has_install_and_url(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("/plugin marketplace add memvara/claude-memvara", text)
        self.assertIn("/plugin install memvara", text)
        self.assertIn(HOSTED, text)
        self.assertNotIn("marketplace add memvara/memvara", text)
        lower = text.lower()
        self.assertIn("claude desktop", lower)
        self.assertIn("paste", lower)

    def test_readme_does_not_offer_npx_as_an_install(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("npx ", text)

    def test_license_is_apache_2(self) -> None:
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", text)
        self.assertIn("Version 2.0", text)


class Hygiene(unittest.TestCase):
    def test_no_npx_in_json(self) -> None:
        for path in ROOT.rglob("*.json"):
            if "node_modules" in path.parts:
                continue
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("npx", raw, path)

    def test_no_app_json_or_commands(self) -> None:
        self.assertFalse((PLUGIN / ".app.json").exists())
        self.assertFalse((PLUGIN / "commands").exists())

    def test_plugin_tree_has_no_stray_files(self) -> None:
        allowed = set(ALLOWED_PLUGIN_FILES)
        for path in SKILL.rglob("*"):
            if path.is_file():
                allowed.add(path.relative_to(PLUGIN))
        found = {
            p.relative_to(PLUGIN) for p in PLUGIN.rglob("*")
            # Running a hook writes bytecode next to it, so __pycache__ appears inside
            # any installed copy. It is generated, never committed (see .gitignore), and
            # failing on it would fail every machine that has used the plugin once.
            if p.is_file() and "__pycache__" not in p.parts
        }
        extra = found - allowed
        self.assertFalse(extra, f"unexpected plugin files: {sorted(extra)}")

    def test_no_memvara_db_or_secret_prefixes(self) -> None:
        # Skill prose may name MEMVARA_DB as the local-init trap. The files
        # the Claude host reads for *this* install must not.
        paths = [
            PLUGIN / ".mcp.json",
            PLUGIN / ".claude-plugin" / "plugin.json",
            ROOT / ".claude-plugin" / "marketplace.json",
            ROOT / "README.md",
        ]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("MEMVARA_DB=", text, path)
            self.assertNotRegex(text, r"\bsm_[a-zA-Z0-9]{8,}", msg=str(path))

    def test_github_repository_field_matches_org(self) -> None:
        env = os.environ.get("GITHUB_REPOSITORY")
        if env:
            self.assertTrue(env.startswith("memvara/"), env)
            self.assertEqual(env, REPO_NAME)


if __name__ == "__main__":
    unittest.main()
