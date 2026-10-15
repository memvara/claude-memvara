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
        try:
            event = json.loads(line)
        except ValueError:
            # A blank line, or the half-written last line of a run that was stopped.
            continue
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
    #: Runs that ended in an error, such as reaching the budget cap. Their answers are
    #: judged as they stand, so a high count explains a low score.
    stopped: int = 0


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
        stopped=sum(run.error for run in runs),
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
    graded = [(int(m[1]), m[2].lower())
              for m in re.finditer(r"^\D*?(\d+)\W+(right|partly|wrong)\b", reply, re.M | re.I)]
    found = dict(graded)
    if len(graded) != count or sorted(found) != list(range(1, count + 1)):
        raise SystemExit(f"The judge did not grade every answer exactly once:\n{reply}")
    return found


def judge() -> None:
    """Grade each question's answers in one call, shuffled and without their setups.

    A question is graded again whenever its set of answers has changed since it was last
    graded, so that runs added later are never left without a verdict.
    """
    for question in load_questions():
        out = DATA / "judge" / f"{question['id']}.json"
        root = str(DATA / "snapshots" / question["repo"])
        answers = [(f"{arm}/{path.stem}",
                    parse_run(path.read_text().splitlines(), question["expected_files"],
                              root).answer)
                   for arm in ARMS
                   for path in sorted((DATA / "runs" / arm).glob(f"{question['id']}-*.jsonl"))]
        if not answers:
            continue
        if out.exists() and set(json.loads(out.read_text())["verdicts"]) == {k for k, _ in answers}:
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
           "| Judged | Stopped early |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for s in summaries.values():
        out.append(f"| {s.arm} | {s.runs} | {s.calls_to_first:.1f} | "
                   f"{s.tokens_to_first:,.0f} | {s.never_opened} | "
                   f"{s.tokens:,.0f} | ${s.cost_usd:.2f} | {s.right:.1f} | {s.judged} | {s.stopped} |")
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
