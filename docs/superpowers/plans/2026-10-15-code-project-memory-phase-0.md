# Code project memory, Phase 0: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Before Phase 1 is built, measure whether notes about a codebase help a coding agent answer real questions. The notes are shown when a session starts and when the agent first touches a file in a component.

**Architecture:** One standard-library script, `bench/code_memory.py`, puts each question to `claude -p` in three setups. A has memvara off. B is memvara as installed today, without its capture hook. C has memvara off and adds hand-written notes through a hook that the same script serves. The script reads the stream-json transcripts, has a model grade the answers without knowing their setups, and prints the gate. Everything with private content lives in `~/.memvara-bench`, outside this public repository.

**Tech Stack:** Python 3 standard library and `unittest`. The `claude` CLI 2.1.289, with `-p`, `--output-format stream-json`, `--settings`, `--plugin-dir` and `--max-budget-usd`. `gh` for the clones.

**Spec:** `docs/superpowers/specs/2026-09-30-code-project-memory-design.md`, section 7, Phase 0.

The code in Tasks 1 to 4 was run in a scratch copy of this repository on 2026-10-15. All 26 tests passed. Each guard was broken on purpose once, and its test failed. A smoke run of all three setups, the judge and the report completed on a two-file repository with Haiku, for about $0.30.

## Global Constraints

- The gate, from the spec: "Build Phase 1 if C, compared with A, cuts tool calls to the first right file by at least 30%, or gets at least 2 more of 10 decision-dependent questions right, and costs no more than 20% more total tokens."
- The questions, from the spec: "Take 10 real questions from past sessions in each of memvara and memvara-cloud. Before any run, write down for each question the files that answer it and the decision it depends on, if any."
- "Each question runs twice in each setup." The answers are judged "against the written expectation without knowing which setup produced it."
- Standard library only. No new dependency.
- `plugin/hooks/` and `plugin/skills/` are vendored from memvara/memvara. Never edit them here.
- This repository is public and memvara-cloud is private. Questions, notes, snapshots, transcripts and verdicts stay in `~/.memvara-bench` and are never committed. The spec gets numbers only, never question text.
- No run may write to the user's memvara store. Setup B runs without the capture hook, and every setup denies the memvara write tools.
- No AI attribution in any commit message, pull request title or pull request body. This includes text that a subagent writes.
- Write plainly in every docstring, comment, commit message and pull request body.
- Never state a tool count in a markdown file here. `test_no_other_count_is_stated_anywhere` scans every tracked `*.md` and `*.json` file.

## Review Focus

1. **A run that hands work to a subagent.** The subagent's tool calls must count, including the one that reads the right file. Pinned by `test_calls_inside_a_subagent_count` in Task 1.
2. **A run that stops at its budget cap or ends in an error.** It must parse without a crash, and its empty answer is judged wrong. Pinned by `test_a_run_that_stopped_at_its_budget_still_parses` in Task 1.
3. **A path that only resembles an expected file**, such as `restore.py`, `store.pyc` or `legacy-memvara/store.py`, and relative paths given to Grep or Bash. Only whole paths may match. Pinned by `test_only_whole_paths_match` in Task 1.
4. **Several touches of one component in a session**, including two file tools in the same reply. The component's notes must appear once. The marker file is created with `O_EXCL`, so two racing calls cannot both win. Pinned by `test_a_component_s_notes_are_added_once_per_session` in Task 3.
5. **A broken notes file, an event without a file path, or a path outside every snapshot.** The hook must add nothing, exit 0 and say why in its log. Pinned by the last two tests of `NotesHook` in Task 3.

## Facts this plan rests on

Each was checked on 2026-10-15 with Claude Code 2.1.289.

- `--settings '{"enabledPlugins":{"memvara@claude-memvara":false}}'` removes memvara from a run. The init event then lists no memvara plugin, no memvara server and no memvara tools, and no memvara hook runs.
- `--plugin-dir <copy>` loads the copy as `memvara@inline`. Its server reports `connected` and its session-start hook runs. With `Stop` removed from the copy's `hooks.json`, `~/.memvara/.hooks/capture.log` did not grow.
- A `SessionStart` or `PreToolUse` command hook that prints `{"hookSpecificOutput": {"hookEventName": ..., "additionalContext": ...}}` reaches the model in `-p` mode. Only SessionStart hook output appears in the stream, so setup C is checked from the hook's own log.
- stream-json sends one `assistant` event per content block. Each event carries the usage of the whole reply, so usage is counted once per `message.id`. The `result` event carries `result`, `total_cost_usd`, `is_error` and `modelUsage`.
- Without `stdin=DEVNULL`, `claude -p` waits 3 seconds for input.
- Claude Code shows the agent the latest commit subjects when a session starts. In the smoke run, setup A answered a "why" question from the subject of the only commit. Task 7 therefore excludes questions whose answer is in a recent commit subject.
- A real judge wrote its verdicts as `1. right`. The verdict parser accepts a decorated label.
- The past-session transcripts hold 538 candidate questions: 397 about memvara and 141 about memvara-cloud.

## File structure

- Create `bench/code_memory.py`. It holds the harness: transcript parsing, the gate, the three setups, the notes hook for setup C, and the `run`, `judge` and `report` commands. It is one file because each piece is small and they share one model of a run. The plugin manifest's `source` is `./plugin`, so `bench/` never ships to users.
- Create `test/test_bench_code_memory.py`. CI already runs everything under `test/`, with `python3 -m unittest discover -s test -v`.
- Modify `docs/superpowers/specs/2026-09-30-code-project-memory-design.md`. Task 10 records the result under Phase 0.
- Outside the repository, `~/.memvara-bench/` holds the data. The module docstring describes its layout.

---

### Task 1: Read a run's transcript, and the gate

**Files:**
- Create: `bench/code_memory.py`
- Create: `test/test_bench_code_memory.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - Module constants that later tasks use: `REPO`, `DATA`, `MODEL`, `ARMS`, `BUDGET_USD`, `MEMVARA`, `READ_ONLY` and `DENIED`.
  - `Run`, a dataclass with these fields: `session`, `memvara`, `tool_calls`, `calls_to_first`, `opened`, `tokens_to_first`, `tokens`, `cost_usd`, `answer` and `error`.
  - `touches(call: dict, expected: list[str], root: str) -> bool`
  - `parse_run(lines: list[str], expected: list[str], root: str) -> Run`
  - `Summary`, a dataclass with these fields: `arm`, `runs`, `calls_to_first`, `never_opened`, `tokens`, `cost_usd`, `right`, `judged` and `tokens_to_first`.
  - `summarize(arm: str, rows: list[tuple[dict, Run, str | None]]) -> Summary`
  - `gate(a: Summary, c: Summary) -> tuple[bool, list[str]]`

- [ ] **Step 1: Write the failing tests**

Create `test/test_bench_code_memory.py`:

```python
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

    def test_memvara_is_seen_in_the_init_event(self):
        self.assertEqual(self.parse(init(), result()).memvara, "")
        loaded = self.parse(init(memvara=("memvara@inline", "connected")), result())
        self.assertEqual(loaded.memvara, "memvara@inline connected")


class Gate(unittest.TestCase):

    def summary(self, calls, right, tokens):
        return cm.Summary("X", 40, calls, 0, tokens, 0.0, right, 40)

    def test_thirty_percent_fewer_calls_passes_within_a_fifth_more_tokens(self):
        self.assertTrue(cm.gate(self.summary(10, 3, 100), self.summary(7, 3, 120))[0])

    def test_more_than_a_fifth_more_tokens_fails_whatever_else_improved(self):
        self.assertFalse(cm.gate(self.summary(10, 3, 100), self.summary(5, 9, 121))[0])

    def test_two_more_decision_questions_right_passes(self):
        self.assertTrue(cm.gate(self.summary(10, 3, 100), self.summary(10, 5, 100))[0])
        self.assertFalse(cm.gate(self.summary(10, 3, 100), self.summary(8, 4.5, 100))[0])

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
```

- [ ] **Step 2: Run the tests and check that they fail**

Run: `python3 -m unittest discover -s test -p "test_bench_code_memory.py" -v`
Expected: an error, `ModuleNotFoundError: No module named 'code_memory'`.

- [ ] **Step 3: Write the implementation**

Create `bench/code_memory.py`:

```python
#!/usr/bin/env python3
"""Phase 0 of the code project memory design: do notes about a codebase help an agent?

The design is docs/superpowers/specs/2026-09-30-code-project-memory-design.md, section 7,
and the plan is docs/superpowers/plans/2026-10-15-code-project-memory-phase-0.md. This
script asks real questions about a repository through `claude -p` in three setups, and
compares the answers:

  A  memvara is turned off.
  B  memvara as it is installed today, without its capture hook, so that no run writes
     to the store.
  C  memvara is turned off, and hand-written notes are shown instead: a brief when the
     session starts, and a component's notes the first time the agent reads or edits a
     file in that component.

Commands:

  python3 bench/code_memory.py run [--arms A,B,C] [--repeats 2] [--only ID,ID]
  python3 bench/code_memory.py judge
  python3 bench/code_memory.py report
  python3 bench/code_memory.py hook      Claude Code calls this in setup C.

`run` and `judge` skip the work that is already done, so either can be stopped and
started again.

The data lives outside this repository. The questions come from private sessions, one of
the repositories is private, and this repository is public. The data is in
~/.memvara-bench, or in the directory that MEMVARA_BENCH_DATA names:

  questions.json               the questions, written before any run
  notes/<repo>.json            the hand-written notes for setup C
  snapshots/<repo>/            a clone of each repository at a fixed commit
  runs/<setup>/<id>-<n>.jsonl  one run's transcript, as `claude -p` streamed it
  judge/<id>.json              the blind verdicts on one question's answers
  hook.log                     one line for every call of the notes hook
  report.md                    the result, and whether it passes the gate
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shlex
import shutil
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("MEMVARA_BENCH_DATA", Path.home() / ".memvara-bench"))
MODEL = os.environ.get("MEMVARA_BENCH_MODEL", "sonnet")
ARMS = ("A", "B", "C")
#: A run that reaches this cap stops, and is judged on what it said by then.
BUDGET_USD = "2"
MEMVARA = "memvara@claude-memvara"
#: Every setup may read the repository and its history, and may change nothing, either in
#: the repository or in the store.
READ_ONLY = ["Read", "Grep", "Glob", "Bash(git log:*)", "Bash(git show:*)",
             "Bash(git grep:*)", "Bash(git blame:*)", "Bash(ls:*)", "Bash(wc:*)"]
DENIED = ["Edit", "Write", "MultiEdit", "NotebookEdit"] + [
    f"mcp__plugin_memvara_memvara__{name}" for name in (
        "memory_add", "memory_add_document", "memory_remember", "memory_end",
        "memory_end_matching", "memory_forget", "memory_forget_matching",
        "memory_delete_document", "memory_link")]
#: The context one reply was given, from the `usage` of an assistant message.
CONTEXT_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
#: Every token of a run, subagents included, from the `modelUsage` of the result.
TOTAL_KEYS = ("inputTokens", "outputTokens", "cacheReadInputTokens",
              "cacheCreationInputTokens")


@dataclass
class Run:
    """What one run did, read from its transcript."""

    session: str = ""
    #: Empty when memvara was not loaded. Otherwise its plugin source and server status,
    #: for example "memvara@inline connected".
    memvara: str = ""
    tool_calls: int = 0
    #: The position of the first tool call that read an expected file, counting from 1.
    #: A run that never read one gets one more than the calls it made.
    calls_to_first: int = 0
    opened: bool = False
    #: The context tokens sent up to and including the reply that made that call.
    tokens_to_first: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    answer: str = ""
    error: bool = False


def touches(call: dict, expected: list[str], root: str) -> bool:
    """True when a tool call reads one of the expected files, named relative to root."""
    args = call.get("input") or {}
    if call.get("name") in ("Read", "Grep"):
        path = args.get("file_path") or args.get("path")
        if not path:
            return False
        real_root = os.path.realpath(root)
        rel = os.path.relpath(os.path.realpath(os.path.join(real_root, path)), real_root)
        return rel in expected
    if call.get("name") == "Bash":
        command = args.get("command", "")
        # ponytail: a path named in a shell command is matched as text, so a root-level
        # expected file also matches a nested file of the same name. Parse the command if
        # that ever matters.
        return any(re.search(rf"(?<![\w.-]){re.escape(path)}(?![\w.-])", command)
                   for path in expected)
    return False


def parse_run(lines: list[str], expected: list[str], root: str) -> Run:
    """Read one run's stream-json transcript."""
    run, seen, context = Run(), set(), 0
    for line in lines:
        if not line.strip():
            continue
        event = json.loads(line)
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            run.session = event.get("session_id", "")
            plugin = next((p for p in event.get("plugins", []) if p.get("name") == "memvara"),
                          None)
            server = next((s for s in event.get("mcp_servers", [])
                           if s.get("name") == "plugin:memvara:memvara"), None)
            if plugin or server:
                run.memvara = (f"{(plugin or {}).get('source', 'no plugin')} "
                               f"{(server or {}).get('status', 'no server')}")
        elif kind == "assistant":
            message = event.get("message", {})
            # Claude Code sends one event per content block of a reply, and each of them
            # carries the usage of the whole reply. Count each reply once.
            key = message.get("id") or line
            if key not in seen:
                seen.add(key)
                usage = message.get("usage", {})
                context += sum(usage.get(k, 0) for k in CONTEXT_KEYS)
            for block in message.get("content", []):
                if block.get("type") != "tool_use":
                    continue
                run.tool_calls += 1
                if not run.opened and touches(block, expected, root):
                    run.opened = True
                    run.calls_to_first, run.tokens_to_first = run.tool_calls, context
        elif kind == "result":
            run.answer = event.get("result") or ""
            run.cost_usd = event.get("total_cost_usd") or 0.0
            run.error = bool(event.get("is_error"))
            run.tokens = sum(usage.get(k, 0)
                             for usage in (event.get("modelUsage") or {}).values()
                             for k in TOTAL_KEYS)
    if not run.opened:
        run.calls_to_first, run.tokens_to_first = run.tool_calls + 1, context
    return run


@dataclass
class Summary:
    """One setup's runs, reduced to the numbers the gate compares."""

    arm: str
    runs: int
    calls_to_first: float
    never_opened: int
    tokens: float
    cost_usd: float
    #: "why" questions answered right. A question asked twice and answered right once
    #: counts as one half.
    right: float
    judged: int
    tokens_to_first: float = 0.0


def summarize(arm: str, rows: list[tuple[dict, Run, str | None]]) -> Summary:
    """rows holds (question, run, verdict) for every run of one setup."""
    runs = [run for _, run, _ in rows]
    answers: dict[str, list[bool]] = {}
    for question, _, verdict in rows:
        if question["kind"] == "why":
            answers.setdefault(question["id"], []).append(verdict == "right")
    return Summary(
        arm=arm,
        runs=len(runs),
        calls_to_first=statistics.mean(run.calls_to_first for run in runs),
        never_opened=sum(not run.opened for run in runs),
        tokens=statistics.mean(run.tokens for run in runs),
        cost_usd=sum(run.cost_usd for run in runs),
        right=sum(sum(right) / len(right) for right in answers.values()),
        judged=sum(verdict is not None for _, _, verdict in rows),
        tokens_to_first=statistics.mean(run.tokens_to_first for run in runs),
    )


def gate(a: Summary, c: Summary) -> tuple[bool, list[str]]:
    """The design's gate for Phase 0: is C enough better than A to build Phase 1?"""
    fewer = (a.calls_to_first - c.calls_to_first) / a.calls_to_first
    more = c.right - a.right
    extra = c.tokens / a.tokens - 1
    lines = [
        f"Tool calls before a right file was read: A {a.calls_to_first:.1f}, "
        f"C {c.calls_to_first:.1f}, a change of {-fewer:+.0%} for C. "
        f"The gate needs -30% or lower.",
        f"Decision questions answered right: A {a.right:.1f}, C {c.right:.1f}, "
        f"a change of {more:+.1f} for C. The gate needs +2.0 or more.",
        f"Tokens per run: A {a.tokens:,.0f}, C {c.tokens:,.0f}, a change of {extra:+.0%} "
        f"for C. The gate allows +20% at most.",
    ]
    return (fewer >= 0.30 or more >= 2) and extra <= 0.20, lines
```

- [ ] **Step 4: Run the tests and check that they pass**

Run: `python3 -m unittest discover -s test -p "test_bench_code_memory.py" -v`
Expected: `Ran 13 tests` and `OK`.

- [ ] **Step 5: Break the guards on purpose and check that the tests fail**

Change `if key not in seen:` to `if True:` in `parse_run`, and run the tests. Expected: `FAILED (failures=1)`, from `test_a_reply_split_into_blocks_counts_its_usage_once`. Restore the line.

Change `(?<![\w.-])` to `(?<!\w)` in `touches`, and run the tests. Expected: `FAILED (failures=1)`, from `test_only_whole_paths_match`. Restore it.

Run the tests once more. Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add bench/code_memory.py test/test_bench_code_memory.py
git commit -m "Add the transcript reader and the gate for the Phase 0 harness"
```

### Task 2: The three setups

**Files:**
- Modify: `bench/code_memory.py` (append)
- Modify: `test/test_bench_code_memory.py` (append)

**Interfaces:**
- Consumes: `Run`, `MODEL`, `BUDGET_USD`, `MEMVARA`, `READ_ONLY`, `DENIED` and `REPO` from Task 1.
- Produces:
  - `command(question: str, arm: str, plugin_copy: Path | None = None) -> list[str]`
  - `plugin_without_capture(dest: Path) -> Path`
  - `check_setup(arm: str, run: Run, briefed: set[str]) -> str | None`

- [ ] **Step 1: Write the failing tests**

Append this to the end of `test/test_bench_code_memory.py`, with two blank lines before it:

```python
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
```

- [ ] **Step 2: Run the tests and check that they fail**

Run: `python3 -m unittest discover -s test -p "test_bench_code_memory.py" -v`
Expected: the six `Setups` tests error with `AttributeError: module 'code_memory' has no attribute 'command'`, or with the same error for `plugin_without_capture` or `check_setup`.

- [ ] **Step 3: Write the implementation**

Append this to the end of `bench/code_memory.py`, with two blank lines before it:

```python
def command(question: str, arm: str, plugin_copy: Path | None = None) -> list[str]:
    """The whole `claude -p` command for one question in one setup."""
    settings: dict = {"enabledPlugins": {MEMVARA: False}}
    extra: list[str] = []
    if arm == "B":
        extra = ["--plugin-dir", str(plugin_copy)]
    elif arm == "C":
        hook = [{"type": "command",
                 "command": f"{shlex.quote(sys.executable)} "
                            f"{shlex.quote(str(Path(__file__).resolve()))} hook"}]
        settings["hooks"] = {
            "SessionStart": [{"hooks": hook}],
            "PreToolUse": [{"matcher": "Read|Edit|MultiEdit|Write", "hooks": hook}],
        }
    elif arm != "A":
        raise ValueError(f"unknown setup {arm!r}")
    # The question goes straight after -p: --allowedTools and --disallowedTools take every
    # word that follows them, up to the next option.
    return ["claude", "-p", question, "--model", MODEL,
            "--output-format", "stream-json", "--verbose", "--no-session-persistence",
            "--permission-mode", "default", "--max-budget-usd", BUDGET_USD,
            "--settings", json.dumps(settings), *extra,
            "--allowedTools", *READ_ONLY, "--disallowedTools", *DENIED]


def plugin_without_capture(dest: Path) -> Path:
    """A copy of this repository's plugin without its capture hook, for setup B."""
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(REPO / "plugin", dest, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = dest / "hooks" / "hooks.json"
    hooks = json.loads(manifest.read_text())
    hooks["hooks"].pop("Stop")
    if "capture" in json.dumps(hooks["hooks"]):
        raise SystemExit("The plugin still registers a capture hook, so setup B would "
                         "write every run to the store. Remove it here first.")
    manifest.write_text(json.dumps(hooks, indent=2))
    return dest


def check_setup(arm: str, run: Run, briefed: set[str]) -> str | None:
    """Why a run did not get the setup it was meant to have, or None when it did."""
    if not run.session:
        return "the transcript has no init event"
    if arm in ("A", "C") and run.memvara:
        return f"memvara was loaded ({run.memvara})"
    if arm == "B" and run.memvara != "memvara@inline connected":
        return f"memvara did not load from the copy ({run.memvara or 'absent'})"
    if arm == "C" and run.session not in briefed:
        return "the notes hook did not add the brief"
    return None
```

- [ ] **Step 4: Run the tests and check that they pass**

Run: `python3 -m unittest discover -s test -p "test_bench_code_memory.py" -v`
Expected: `Ran 19 tests` and `OK`.

- [ ] **Step 5: Break the guards on purpose and check that the tests fail**

Change `hooks["hooks"].pop("Stop")` to `hooks["hooks"].pop("Nothing", None)`, and run the tests. Expected: `FAILED (errors=1)`. The `SystemExit` comes from `test_the_copy_keeps_memvara_and_drops_capture`, which reads this repository's real `plugin/hooks/hooks.json`. Restore the line.

Remove `"memory_add", ` and `"memory_remember", ` from `DENIED`, and run the tests. Expected: `FAILED (failures=1)`. Restore them.

Run the tests once more. Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add bench/code_memory.py test/test_bench_code_memory.py
git commit -m "Add the three Phase 0 setups: memvara off, memvara without capture, and notes"
```

### Task 3: The notes hook for setup C

**Files:**
- Modify: `bench/code_memory.py` (append)
- Modify: `test/test_bench_code_memory.py` (append)

**Interfaces:**
- Consumes: `DATA` from Task 1.
- Produces:
  - `locate(path: str) -> tuple[str, str] | None`
  - `first_touch(session: str, component: str) -> bool`
  - `hook(event: dict) -> dict | None`
  - `run_hook() -> int`
  - `briefed() -> set[str]`
- The notes file `~/.memvara-bench/notes/<repo>.json` has this shape: `{"brief": str, "components": {"<directory relative to the repository root>": str}}`.

- [ ] **Step 1: Write the failing tests**

Append this to the end of `test/test_bench_code_memory.py`, with two blank lines before it:

```python
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
```

- [ ] **Step 2: Run the tests and check that they fail**

Run: `python3 -m unittest discover -s test -p "test_bench_code_memory.py" -v`
Expected: the four `NotesHook` tests error with `AttributeError: module 'code_memory' has no attribute 'hook'`, or with the same error for `briefed` or `run_hook`.

- [ ] **Step 3: Write the implementation**

Append this to the end of `bench/code_memory.py`, with two blank lines before it:

```python
def locate(path: str) -> tuple[str, str] | None:
    """(repository, path inside it) for a path in one of the snapshots, else None."""
    snapshots = DATA / "snapshots"
    if not path or not snapshots.is_dir():
        return None
    path = os.path.realpath(path)
    for snapshot in snapshots.iterdir():
        root = os.path.realpath(snapshot)
        if path == root:
            return snapshot.name, ""
        if path.startswith(root + os.sep):
            return snapshot.name, path[len(root) + 1:]
    return None


def first_touch(session: str, component: str) -> bool:
    """True the first time a session touches a component, even when two calls race."""
    marker = DATA / "hook-state" / session / component.replace("/", "%2F")
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL))
    except FileExistsError:
        return False
    return True


def hook(event: dict) -> dict | None:
    """Setup C's reply to one hook event, or None to add nothing."""
    name = event.get("hook_event_name")
    if name == "SessionStart":
        found = locate(event.get("cwd", ""))
    elif name == "PreToolUse":
        found = locate((event.get("tool_input") or {}).get("file_path", ""))
    else:
        return None
    text, component = None, None
    if found:
        notes = json.loads((DATA / "notes" / f"{found[0]}.json").read_text())
        if name == "SessionStart":
            text = notes.get("brief")
        else:
            inside = [c for c in notes.get("components", {})
                      if found[1] == c or found[1].startswith(c + "/")]
            component = max(inside, key=len, default=None)
            if component and first_touch(event.get("session_id", ""), component):
                text = notes["components"][component]
    with open(DATA / "hook.log", "a") as log:
        log.write(json.dumps({"session": event.get("session_id"), "event": name,
                              "component": component, "injected": bool(text)}) + "\n")
    if not text:
        return None
    return {"hookSpecificOutput": {"hookEventName": name, "additionalContext": text}}


def run_hook() -> int:
    """Claude Code's entry point in setup C. It never blocks a tool and never fails."""
    try:
        reply = hook(json.load(sys.stdin))
    except Exception as exc:  # noqa: BLE001 - a broken note must not break the run
        try:
            with open(DATA / "hook.log", "a") as log:
                log.write(json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n")
        except OSError:
            pass
        return 0
    if reply:
        print(json.dumps(reply))
    return 0


def briefed() -> set[str]:
    """The sessions that the notes hook gave a brief to."""
    log = DATA / "hook.log"
    if not log.exists():
        return set()
    events = (json.loads(line) for line in log.read_text().splitlines() if line.strip())
    return {e["session"] for e in events
            if e.get("event") == "SessionStart" and e.get("injected")}
```

- [ ] **Step 4: Run the tests and check that they pass**

Run: `python3 -m unittest discover -s test -p "test_bench_code_memory.py" -v`
Expected: `Ran 23 tests` and `OK`.

- [ ] **Step 5: Break the guard on purpose and check that the test fails**

Change `if component and first_touch(` to `if component or first_touch(`, and run the tests. Expected: `FAILED`, from `test_a_component_s_notes_are_added_once_per_session`. Restore it, and run the tests once more. Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add bench/code_memory.py test/test_bench_code_memory.py
git commit -m "Add the notes hook that setup C uses"
```

### Task 4: The run, judge and report commands

**Files:**
- Modify: `bench/code_memory.py` (append)
- Modify: `test/test_bench_code_memory.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1 to 3.
- Produces:
  - `load_questions() -> list[dict]`
  - `run(arms: list[str], repeats: int, only: set[str] | None) -> None`
  - `parse_verdicts(reply: str, count: int) -> dict[int, str]`
  - `judge() -> None`
  - `head(repo: Path) -> str`
  - `report() -> str`
  - `main(argv: list[str]) -> int`
- Each entry in `~/.memvara-bench/questions.json` has these keys:
  - `id`: for example `core-01` or `cloud-01`, with no dot.
  - `repo`: the name of a directory under `snapshots/`.
  - `kind`: `"locate"` or `"why"`.
  - `question`
  - `expected_files`: paths relative to the repository root.
  - `decision`: the short sha and one sentence for a "why" question, or `null`.
  - `answer_key`

- [ ] **Step 1: Write the failing tests**

Append this to the end of `test/test_bench_code_memory.py`, with two blank lines before it:

```python
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
```

- [ ] **Step 2: Run the tests and check that they fail**

Run: `python3 -m unittest discover -s test -p "test_bench_code_memory.py" -v`
Expected: the `RunCommand` and `Verdicts` tests error with `AttributeError: module 'code_memory' has no attribute 'run'`, or with the same error for `parse_verdicts`.

- [ ] **Step 3: Write the implementation**

Append this to the end of `bench/code_memory.py`, with two blank lines before it:

```python
def load_questions() -> list[dict]:
    return json.loads((DATA / "questions.json").read_text())


def run(arms: list[str], repeats: int, only: set[str] | None) -> None:
    """Run every question in every setup, skipping the runs that already exist."""
    copy = plugin_without_capture(DATA / "plugin-b") if "B" in arms else None
    for question in load_questions():
        if only and question["id"] not in only:
            continue
        root = DATA / "snapshots" / question["repo"]
        for arm in arms:
            for n in range(1, repeats + 1):
                out = DATA / "runs" / arm / f"{question['id']}-{n}.jsonl"
                if out.exists():
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                partial = out.with_suffix(".partial")
                with open(partial, "w") as stdout, open(out.with_suffix(".err"), "w") as stderr:
                    subprocess.run(command(question["question"], arm, copy), cwd=root,
                                   stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
                result = parse_run(partial.read_text().splitlines(),
                                   question["expected_files"], str(root))
                problem = check_setup(arm, result, briefed())
                name = f"{arm} {question['id']}-{n}"
                if problem:
                    partial.rename(out.with_suffix(".invalid"))
                    print(f"{name}: not counted, because {problem}")
                    continue
                partial.rename(out)
                print(f"{name}: {result.tool_calls} tool calls, right file at call "
                      f"{result.calls_to_first}, ${result.cost_usd:.2f}")


JUDGE = """You are grading answers to one question about a codebase. Grade each answer only
against the answer key. Do not reward length, confidence or style.

Question: {question}

Answer key, which says what a right answer must contain: {key}

Reply with one line for each answer and nothing else, in this form:
<label> <right|partly|wrong> <one short reason>

{answers}"""


def parse_verdicts(reply: str, count: int) -> dict[int, str]:
    """The judge's verdict for each label, or an error naming what it left out."""
    # Judges decorate the label ("1.", "**1**", "1)"), so anything but a digit may surround it.
    found = {int(m[1]): m[2].lower()
             for m in re.finditer(r"^\D*?(\d+)\W+(right|partly|wrong)\b", reply, re.M | re.I)}
    if sorted(found) != list(range(1, count + 1)):
        raise SystemExit(f"The judge did not grade every answer exactly once:\n{reply}")
    return found


def judge() -> None:
    """Grade each question's answers in one call, shuffled and without their setups."""
    for question in load_questions():
        out = DATA / "judge" / f"{question['id']}.json"
        if out.exists():
            continue
        root = str(DATA / "snapshots" / question["repo"])
        answers = [(f"{arm}/{path.stem}",
                    parse_run(path.read_text().splitlines(), question["expected_files"],
                              root).answer)
                   for arm in ARMS
                   for path in sorted((DATA / "runs" / arm).glob(f"{question['id']}-*.jsonl"))]
        if not answers:
            continue
        random.Random(question["id"]).shuffle(answers)
        shown = "\n\n".join(f'<answer label="{i}">\n{text}\n</answer>'
                            for i, (_, text) in enumerate(answers, 1))
        prompt = JUDGE.format(question=question["question"], key=question["answer_key"],
                              answers=shown)
        done = subprocess.run(
            ["claude", "-p", prompt, "--model", MODEL, "--output-format", "json",
             "--no-session-persistence", "--settings",
             json.dumps({"enabledPlugins": {MEMVARA: False}}), "--tools", ""],
            cwd=DATA, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True)
        reply = json.loads(done.stdout)["result"]
        verdicts = parse_verdicts(reply, len(answers))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "reply": reply,
            "verdicts": {key: verdicts[i] for i, (key, _) in enumerate(answers, 1)},
        }, indent=2))
        print(f"{question['id']}: graded {len(answers)} answers")


def head(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip() or "no commit"


def report() -> str:
    """The result table and the gate, written to report.md and returned."""
    questions = {q["id"]: q for q in load_questions()}
    verdicts: dict[str, str] = {}
    for path in (DATA / "judge").glob("*.json"):
        verdicts.update(json.loads(path.read_text())["verdicts"])
    summaries = {}
    for arm in ARMS:
        rows = []
        for path in sorted((DATA / "runs" / arm).glob("*.jsonl")):
            question = questions[path.stem.rsplit("-", 1)[0]]
            root = str(DATA / "snapshots" / question["repo"])
            rows.append((question,
                         parse_run(path.read_text().splitlines(),
                                   question["expected_files"], root),
                         verdicts.get(f"{arm}/{path.stem}")))
        if rows:
            summaries[arm] = summarize(arm, rows)
    shas = ", ".join(f"{snapshot.name} at {head(snapshot)}"
                     for snapshot in sorted((DATA / "snapshots").iterdir()))
    out = ["# Phase 0 result", "", f"Model: {MODEL}. Snapshots: {shas}.", "",
           "| Setup | Runs | Tool calls before a right file | Tokens before a right file | "
           "Never read one | Tokens per run | Cost at list price | Decision questions right "
           "| Judged |",
           "|---|---|---|---|---|---|---|---|---|"]
    for s in summaries.values():
        out.append(f"| {s.arm} | {s.runs} | {s.calls_to_first:.1f} | "
                   f"{s.tokens_to_first:,.0f} | {s.never_opened} | "
                   f"{s.tokens:,.0f} | ${s.cost_usd:.2f} | {s.right:.1f} | {s.judged} |")
    if "A" in summaries and "C" in summaries:
        passed, lines = gate(summaries["A"], summaries["C"])
        out += ["", f"Gate: {'passed' if passed else 'not passed'}.", ""]
        out += [f"- {line}" for line in lines]
    text = "\n".join(out) + "\n"
    (DATA / "report.md").write_text(text)
    return text


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run", help="run the questions")
    runner.add_argument("--arms", default="A,B,C")
    runner.add_argument("--repeats", type=int, default=2)
    runner.add_argument("--only", default="", help="question ids, separated by commas")
    sub.add_parser("judge", help="grade the answers without their setups")
    sub.add_parser("report", help="print the result and the gate")
    sub.add_parser("hook", help="setup C's notes hook, which Claude Code calls")
    args = parser.parse_args(argv)
    if args.command == "hook":
        return run_hook()
    if args.command == "run":
        run(args.arms.split(","), args.repeats,
            set(filter(None, args.only.split(","))) or None)
    elif args.command == "judge":
        judge()
    else:
        print(report(), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests and check that they pass**

Run: `python3 -m unittest discover -s test -p "test_bench_code_memory.py" -v`
Expected: `Ran 26 tests` and `OK`.

- [ ] **Step 5: Break the guard on purpose and check that the test fails**

Delete the two lines `if out.exists():` and `continue` in `run`, and run the tests. Expected: `FAILED (failures=1)`, from `test_a_run_is_kept_once_and_not_repeated`. Restore them.

- [ ] **Step 6: Run the repository's whole test suite**

This is a code change, so the full gate runs: `python3 -m unittest discover -s test -v`.
Expected: the last line says `OK`. Read the whole verdict line. If it says `OK (skipped=N)`, write down which tests were skipped and why, because a skip is not a pass.

- [ ] **Step 7: Smoke-run the harness on a two-file repository**

This makes real `claude -p` calls on Haiku, for about $0.30. Run each line:

```bash
D=$(mktemp -d)
mkdir -p "$D/snapshots/demo/app/store" "$D/notes"
git -C "$D/snapshots/demo" init -q
printf 'def save(x):\n    return x\n' > "$D/snapshots/demo/app/store/save.py"
git -C "$D/snapshots/demo" add -A
git -C "$D/snapshots/demo" -c user.name=t -c user.email=t@t commit -qm "Store values as they are given, because callers already encode them"
printf '%s\n' '{"brief": "app/store saves values.", "components": {"app/store": "app/store keeps values exactly as given. Callers encode them first."}}' > "$D/notes/demo.json"
printf '%s\n' '[{"id": "demo-01", "repo": "demo", "kind": "why", "question": "Read app/store/save.py, then say why save() does not encode values.", "expected_files": ["app/store/save.py"], "decision": null, "answer_key": "Callers already encode the values, so the store keeps them as given."}]' > "$D/questions.json"
wc -c < ~/.memvara/.hooks/capture.log
MEMVARA_BENCH_DATA="$D" MEMVARA_BENCH_MODEL=haiku python3 bench/code_memory.py run --repeats 1
MEMVARA_BENCH_DATA="$D" MEMVARA_BENCH_MODEL=haiku python3 bench/code_memory.py judge
MEMVARA_BENCH_DATA="$D" MEMVARA_BENCH_MODEL=haiku python3 bench/code_memory.py report
cat "$D/hook.log"
wc -c < ~/.memvara/.hooks/capture.log
```

Expected:
- `run` prints three lines, `A demo-01-1`, `B demo-01-1` and `C demo-01-1`. Each says `right file at call 1`, and none says `not counted`.
- `judge` prints `demo-01: graded 3 answers`.
- `report` prints a table with rows A, B and C.
- `hook.log` holds one `SessionStart` line and one `PreToolUse` line for component `app/store`, and both have `"injected": true`.
- The two `capture.log` sizes are equal.

- [ ] **Step 8: Commit**

```bash
git add bench/code_memory.py test/test_bench_code_memory.py
git commit -m "Add the run, judge and report commands to the Phase 0 harness"
```

### Task 5: Pull request and code review

**Files:** none new. The branch also carries the spec and this plan.

- [ ] **Step 1: Push and open the pull request**

```bash
git push -u origin claude/plugin-file-context-memory-f6e235
gh pr create --title "Add the design for code project memory and its Phase 0 harness" --body-file "$TMPDIR/pr-body.md"
```

Write the body to `$TMPDIR/pr-body.md` first, in plain sentences. It should say:
- what the design proposes, and that the plugin owns the decisions taken from git history for now;
- what the harness measures, and that all data stays in `~/.memvara-bench`;
- the commands that ran, with their `Ran N tests` and `OK` lines quoted.

No AI attribution anywhere.

- [ ] **Step 2: Review the pull request**

1. Switch the session model to the latest Sonnet.
2. Run `/code-review high <the number gh printed> --fix`.
3. Switch the model back.

Fix every finding on the same branch, then run the whole suite again, as in Task 4, Step 6. When a finding is wrong, give the reason in the pull request body. The body says that a review ran, at what effort, and what it found. It never names a model.

- [ ] **Step 3: Push the fixes**

```bash
git push
```

### Task 6: Repository snapshots and hand-written notes

**Files:** outside the repository only.

- [ ] **Step 1: Clone the two repositories**

```bash
mkdir -p ~/.memvara-bench/snapshots ~/.memvara-bench/notes
gh repo clone memvara/memvara ~/.memvara-bench/snapshots/memvara
gh repo clone memvara/memvara-cloud ~/.memvara-bench/snapshots/memvara-cloud
git -C ~/.memvara-bench/snapshots/memvara rev-parse --short HEAD
git -C ~/.memvara-bench/snapshots/memvara-cloud rev-parse --short HEAD
git -C ~/.memvara-bench/snapshots/memvara remote get-url origin
git -C ~/.memvara-bench/snapshots/memvara-cloud remote get-url origin
```

Expected: two short shas, and origins on `github.com/memvara/...`. Setup B finds the project's memory through that remote. Do not pull in these clones until Task 9 is finished. The report prints their shas.

- [ ] **Step 2: Have a subagent write the notes for each repository**

The notes stand in for what Phase 1 would write. So that they cannot be shaped around the questions, they are written before any question is chosen, by a subagent that never sees one. Dispatch one general-purpose subagent per repository with this brief, replacing `<repo>`:

> You are writing notes about a codebase for an experiment. The repository is at `~/.memvara-bench/snapshots/<repo>`. Do not change anything in it. Do not use any memvara tool, and do not read anything under `~/.claude` or `~/.memvara-bench`, except the snapshot and the notes file you write.
>
> Follow sections 5.3 and 5.4 of the design at `docs/superpowers/specs/2026-09-30-code-project-memory-design.md`, in the claude-memvara checkout you start in.
>
> Components:
> - Pick at most 25 components: the top-level source directories, or the packages that a manifest declares as workspace members.
> - Name each one by its directory relative to the repository root, without a trailing slash.
> - Read READMEs, manifests, entry points and a small sample of files. Do not read every file.
>
> Decisions:
> - Read the last 300 commits on `main` with `git log -300 --format='%h %cs %s%n%b%n--' main`.
> - Keep a commit only when its message states a choice and the reason for it.
> - Skip commits that have no body, dependency bumps, releases and reverts.
> - Give each kept decision to the component that holds most of the commit's changed paths, using `git show --name-only --format= <sha>`. When no component holds a majority, give it to the brief.
> - Read commit messages and lists of changed paths only, never diffs.
>
> Write `~/.memvara-bench/notes/<repo>.json` in this shape:
>
> ```json
> {"brief": "...", "components": {"<directory>": "..."}}
> ```
>
> - **The brief** has one line per component, in the form `<directory>: <purpose in one sentence>`. Then come up to five conventions, and then the five most recent decisions, each with its date and short sha. Keep it to about 1,600 characters.
> - **Each component's notes** start with its purpose in one sentence. Then come at most four notes, chosen from its decisions, rejected alternatives, known defects and conventions. A note taken from a commit ends with the short sha in parentheses. Keep each component's notes to about 1,200 characters.
> - Write plain sentences. Never copy a secret, a credential or personal data.
>
> Report back the number of components, the number of decisions kept, and the size of the brief in characters.

- [ ] **Step 3: Check the sizes**

```bash
python3 -c "
import json, pathlib
for f in sorted(pathlib.Path.home().joinpath('.memvara-bench/notes').glob('*.json')):
    n = json.loads(f.read_text())
    big = {k: len(v) for k, v in n['components'].items() if len(v) > 1300}
    print(f.stem, 'brief', len(n['brief']), 'components', len(n['components']), 'over 1300:', big)
"
```

Expected: a brief of about 1,700 characters or less, 25 components or fewer, and no component over 1,300 characters. If either limit is exceeded, send the subagent back to cut, and do not cut by hand.

- [ ] **Step 4: Check that the hook reads the real notes**

```bash
echo "{\"hook_event_name\": \"SessionStart\", \"session_id\": \"check\", \"cwd\": \"$HOME/.memvara-bench/snapshots/memvara\"}" | python3 bench/code_memory.py hook
```

Expected: one line of JSON, with the memvara brief as `additionalContext`.

### Task 7: The question set

**Files:** `~/.memvara-bench/questions.json`, outside the repository.

- [ ] **Step 1: List the candidates**

Save this as `~/.memvara-bench/candidates.py` and run `python3 ~/.memvara-bench/candidates.py > ~/.memvara-bench/candidates.jsonl`:

```python
"""List questions the user asked in past sessions about memvara and memvara-cloud."""
import json
import pathlib
import re

PROJECTS = pathlib.Path.home() / ".claude" / "projects"
ASKING = re.compile(r"^(how|why|where|what|which|when|is|are|does|do|can|could|explain)\b", re.I)


def repo(project: str) -> str | None:
    if project.startswith("-Applications-workstation-memvara-cloud"):
        return "memvara-cloud"
    if (project == "-Applications-workstation-memvara"
            or project.startswith("-Applications-workstation-memvara--")
            or project.startswith("-Applications-workstation-agent-memory")):
        return "memvara"
    return None


def text_of(message: dict) -> str | None:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(b.get("type") == "text" for b in content):
        return "\n".join(b.get("text", "") for b in content)
    return None


out = []
for transcript in PROJECTS.glob("*/*.jsonl"):
    name = repo(transcript.parent.name)
    if not name:
        continue
    for line in transcript.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") != "user" or event.get("isMeta"):
            continue
        text = (text_of(event.get("message") or {}) or "").strip()
        if (not 15 <= len(text) <= 600 or text.startswith("<")
                or "session is being continued" in text):
            continue
        if "?" in text or ASKING.match(text):
            out.append({"repo": name, "date": event.get("timestamp", "")[:10],
                        "session": transcript.stem, "text": text})
out.sort(key=lambda c: c["date"])
for c in out:
    print(json.dumps(c))
```

Expected: about 540 lines, each one a JSON object with `repo`, `date`, `session` and `text`.

- [ ] **Step 2: Choose 10 questions per repository by rule**

Go through the candidates in date order. Keep a candidate only if all of these hold:

1. It asks how the code works, where something is, or why the code is the way it is.
2. It can be understood without the conversation before it. You may replace "this" or "it" with the thing it names, and nothing more.
3. The snapshot can still answer it: the code it asks about still exists at the snapshot's sha.
4. Its answer is not in any of the 10 latest commit subjects (`git log -10 --format=%s`). Claude Code shows those to every setup.

Label each kept question `locate` or `why`. A `why` question is one whose answer depends on a decision recorded in git history or in the repository's docs. Take the first five of each kind for each repository. If a repository has fewer than five `why` questions, take more `locate` questions to reach 10, and note the shortfall for the report.

- [ ] **Step 3: Write the expectations before any run**

For each question, find the answer in the snapshot with `git grep`, `git log -S` and the docs. Then write its entry in `~/.memvara-bench/questions.json` with the keys listed in Task 4. Use `core-01` to `core-10` for memvara and `cloud-01` to `cloud-10` for memvara-cloud. Number each repository's `locate` questions first and its `why` questions last, so that `core-10` and `cloud-10` are always `why` questions. Check that every expected file exists at the snapshot:

```bash
python3 -c "
import json, pathlib, subprocess
home = pathlib.Path.home() / '.memvara-bench'
for q in json.loads((home / 'questions.json').read_text()):
    for f in q['expected_files']:
        r = subprocess.run(['git', '-C', str(home / 'snapshots' / q['repo']), 'ls-files', '--error-unmatch', f], capture_output=True)
        print(q['id'], f, 'ok' if r.returncode == 0 else 'MISSING')
"
```

Expected: every line ends in `ok`.

- [ ] **Step 4: Checkpoint. The user reviews the questions.**

Show the user the 20 questions. For each one, show its kind, its expected files and its answer key. Wait for their confirmation. Nothing runs before they confirm, because the answer keys decide the result.

### Task 8: Pilot

**Files:** outside the repository only.

- [ ] **Step 1: Run one `locate` question and one `why` question in each setup, once**

```bash
wc -c < ~/.memvara/.hooks/capture.log
python3 bench/code_memory.py run --repeats 1 --only core-01,cloud-10
wc -c < ~/.memvara/.hooks/capture.log
```

Expected:
- six lines, none of which says `not counted`;
- equal `capture.log` sizes;
- `tail ~/.memvara-bench/hook.log` shows `"injected": true` for the two setup C sessions.

These runs count as the first repeat. They are not thrown away.

- [ ] **Step 2: Checkpoint. Report the cost before the full run.**

Tell the user:
- the cost of each pilot run;
- the mean cost of a run;
- the projected total: the mean times 120, plus 20 judge calls.

Each run is capped at $2, so the worst case is $240 at list price. The runs use Sonnet unless `MEMVARA_BENCH_MODEL` says otherwise. Wait for a go-ahead.

### Task 9: The full run, judging and the report

**Files:** outside the repository only.

- [ ] **Step 1: Run every question twice in every setup**

Run: `python3 bench/code_memory.py run`
It resumes, so the pilot runs are not repeated. A line that says `not counted` names the reason. Find out why before you run it again: a run moved aside because setup B did not connect is a finding, not noise. Run the command again until it prints nothing new.

- [ ] **Step 2: Judge**

Run: `python3 bench/code_memory.py judge`
Expected: one `graded 6 answers` line per question.

- [ ] **Step 3: Check the judge by hand**

Pick six verdicts, two from each setup. Read the run's answer and the answer key yourself. If you disagree with the judge on two or more of the six, stop and tell the user, because the result cannot then rest on the judge.

- [ ] **Step 4: Report**

Run: `python3 bench/code_memory.py report`
It writes `~/.memvara-bench/report.md` and prints the table and the gate.

### Task 10: Record the result

**Files:**
- Modify: `docs/superpowers/specs/2026-09-30-code-project-memory-design.md`, section 7, Phase 0.

- [ ] **Step 1: Add the result to the spec**

Under Phase 0, add a paragraph headed `Result, <date>`. It gives:
- the model and the two snapshot shas;
- the table from `report.md`;
- the gate verdict, with its three lines;
- the total cost;
- any shortfall in `why` questions.

It does not include question text, because this repository is public.

- [ ] **Step 2: Run the checks that a markdown change can affect**

Run: `python3 -m unittest discover -s test -k ToolCount -v`
Expected: `Ran 7 tests` and `OK`.

- [ ] **Step 3: Commit and push**

```bash
git add docs/superpowers/specs/2026-09-30-code-project-memory-design.md
git commit -m "Record the Phase 0 result for code project memory"
git push
```

- [ ] **Step 4: Write the decision to Confluence**

First make one cheap Atlassian call to check that the tool works. If it does not, run `docker mcp oauth ls`. A line `atlassian-remote | not authorized` means the user has to run `docker mcp oauth authorize atlassian-remote`. Until then, save the page under `local/` in this repository.

The page is titled `<date> Phase 0 of code project memory: <build Phase 1, or stop>`, under the "Decisions" page, id 9601025. It says:
- the decision;
- the date;
- the alternatives;
- the numbers from the report;
- what changes for users;
- what is still open;
- a link to the pull request.

- [ ] **Step 5: Write it to memvara**

Call `memory_remember` with:
- subject `claude_memvara`;
- predicate `decided`;
- object: the verdict, with its three numbers and the pull request number.
