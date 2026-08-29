"""Ask GitHub whether a stored defect has been fixed, and say so at the top of a session.

`capture.py` can open a defect and has no vocabulary for closing one. There is no `end`,
no `forget` and no `true_until` anywhere in these hooks, so a `known_defect` claim is
written once and answers present-tense questions forever. That is not a cardinality
problem and no vocabulary change fixes it: `packs/engineering.toml` declares `known_defect`
as `cardinality = "many"` deliberately -- a project has many open defects and a new one
must not erase the others -- so a defect is never superseded by another defect. It is ended
by an event in the world, and nothing here was watching for one.

Measured on this machine on 2026-08-30, six claims were closed by hand in one sitting; five
had gone stale by mechanism rather than by anyone's mistake, and one had been stale for four
days *with its own closing instruction stored beside it*, naming the claim id and the right
verb, written deliberately at confidence 1.0. Knowing how to close a claim was never the
gap. Nothing read a memory looking for work to do.

**This proposes and never closes.** A wrong `memory_end` records a false reason for a change
that nothing downstream can detect -- the store's own rule -- and the input here is a
subprocess that can answer wrongly for reasons that have nothing to do with the claim: no
`gh`, no network, no auth, a renamed repository. So the sweep resolves refs and writes
candidates to a file; `session_start.py` renders them; a person or a model decides. Closing
stays one tool call away and stays a decision.

It compares a claim against its **referent** -- the pull request itself -- rather than
against another sentence in the same store, which is the property `CLAUDE.md` names as the
one that makes a guard mean anything.

Two hooks, split the way the existing ones are. `capture.py` runs `async` with a 120s
budget and already opens a store, so the network half lives there and is rate-limited to
once every `SWEEP_EVERY_SEC`. `session_start.py` is synchronous with a 20s budget, so it
only reads the file this leaves behind.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

#: Beside the store rather than in the plugin, for `write.LOG`'s reason: the plugin
#: directory is replaced wholesale on update, and a cache that disappears on upgrade sends
#: every install back to GitHub for answers it already had.
STATE = Path.home() / ".memvara" / ".hooks" / "sweep-state.json"

#: How often the network half runs. A merged pull request does not un-merge, so the useful
#: work is almost entirely on the first run; this is the ceiling on how often a store with
#: nothing new to say pays a subprocess for saying it.
SWEEP_EVERY_SEC = 6 * 60 * 60

#: How long a candidate stays quiet after being shown. Long enough that ignoring one does
#: not cost a line every session -- a block that reappears unchanged is a block the eye
#: learns to skip, which is the failure mode of every reminder ever written.
REMIND_AFTER_SEC = 7 * 24 * 60 * 60

#: Refs resolved per sweep. Terminal answers are cached forever, so this bounds the first
#: run rather than the steady state.
MAX_PROBES = 12

#: Candidates rendered at once. The block is an opening brief, not a backlog.
MAX_SHOWN = 5

GH_TIMEOUT_SEC = 10

#: Which GitHub repository a claim's subject is about, for the bare `#123` refs that make
#: up most of what is stored. A subject not named here yields no bare-ref lookups, which
#: costs recall on that subject and never guesses a repository.
#:
#: `agent-memory` is here because it is this machine's checkout name for `memvara/memvara`
#: -- verified against the checkout's own `origin`, not assumed from the directory name.
REPOS: "dict[str, str]" = {
    "memvara": "memvara/memvara",
    "agent-memory": "memvara/memvara",
    "agent_memory": "memvara/memvara",
    "memvara_cloud": "memvara/memvara-cloud",
    "memvara-cloud": "memvara/memvara-cloud",
    "memvara_web": "memvara/memvara-web",
    "memvara-web": "memvara/memvara-web",
    "claude-memvara": "memvara/claude-memvara",
    "claude_memvara": "memvara/claude-memvara",
    "codex-memvara": "memvara/codex-memvara",
    "codex_memvara": "memvara/codex-memvara",
}

#: The query the sweep asks for. Predicates rather than prose: `known_defect` is what the
#: hook's own vocabulary writes, and the engineering pack folds `known_issue`/`known_bug`
#: onto it on the read path.
QUERY = "known defect known issue blocked by"
SEARCH_K = 40

#: `owner/repo#123`, `#123`, and a full pull-request or issue URL. Anchored on a boundary
#: at the front so `v1.2#3` and a markdown heading are not read as refs.
_QUALIFIED = re.compile(r"(?<![\w/-])([A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*)#(\d{1,6})(?!\d)")
_URL = re.compile(r"https?://github\.com/([A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*)"
                  r"/(?:pull|issues)/(\d{1,6})(?!\d)")
_BARE = re.compile(r"(?<![\w/#-])#(\d{1,6})(?!\d)")

#: A claim as `memory_search` renders it: `1. [id=cl_… semantic relevance=0.44] subject …`.
_ROW = re.compile(r"^\s*\d+\.\s*\[id=(cl_[0-9a-fA-F]+)[^\]]*\]\s*(.+)$")

#: What makes a resolved ref mean the defect is over. `MERGED` is the strong one. A pull
#: request `CLOSED` unmerged means the fix was abandoned, which says nothing about the
#: defect, so it is deliberately not here -- see `test_an_abandoned_pull_request_is_not_a_fix`.
TERMINAL = ("MERGED",)


def _log(line: str) -> None:
    """Append to the capture log, or give up. Never raises into a hook."""
    try:
        from .write import log as capture_log

        capture_log(f"sweep {line}")
    except Exception:
        pass


def read_state() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(state: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        # A lost cache costs repeated `gh` calls, not correctness.
        pass


def refs(subject: str, text: str) -> "list[str]":
    """Every GitHub ref in one claim, as `owner/repo#123`, in first-seen order.

    A bare `#123` is resolved through `REPOS` using the claim's own subject, because that
    is the only thing in the row that says which repository it is about. An unmapped
    subject contributes no bare refs rather than a guess: a wrong repository resolves to
    somebody else's pull request, which is a confident answer about the wrong thing.

    **And the subject is not always what the sentence means, so a claim that names a second
    repository contributes no bare refs either.** Found against the real store rather than
    reasoned about: a status claim under subject `memvara` reads "memvara #79 stacked to
    #80 to #82 to #83; memvara-cloud #181, #182, #183", and resolving all seven through the
    subject turned three memvara-cloud pull requests into memvara ones. Those numbers exist
    in both repositories, so the mistake resolves cleanly and reports a merged pull request
    that has nothing to do with the claim. Ambiguity is refused rather than guessed at.
    """
    out: "list[str]" = []

    def add(owner_repo: str, number: str) -> None:
        ref = f"{owner_repo}#{int(number)}"
        if ref not in out:
            out.append(ref)

    for match in _URL.finditer(text):
        add(match.group(1), match.group(2))
    for match in _QUALIFIED.finditer(text):
        add(match.group(1), match.group(2))
    home = REPOS.get(subject.strip())
    if home and not _names_another_repo(text, home):
        for match in _BARE.finditer(text):
            add(home, match.group(1))
    return out


def _names_another_repo(text: str, home: str) -> bool:
    """Whether the claim mentions a repository other than the one its subject implies."""
    for alias, owner_repo in REPOS.items():
        if owner_repo == home:
            continue
        if re.search(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", text):
            return True
    return False


def rows(rendered: str) -> "list[tuple[str, str]]":
    """`(claim_id, text)` for each row of a `memory_search` block.

    Text-shaped because that is what the hosted route returns; the local route is put into
    the same shape by `search` below, so there is one parser and one set of tests rather
    than two of each that can disagree.
    """
    out = []
    for line in rendered.splitlines():
        match = _ROW.match(line)
        if match:
            out.append((match.group(1), match.group(2).strip()))
    return out


def search(store: object, hosted: bool) -> str:
    """A `memory_search` block, from whichever backend this is.

    The hosted client renders it already. The library returns objects, so they are put into
    the same rendering here -- duck-typed across the attribute names a hit may carry rather
    than importing `memvara.types`, which would pull ~95ms of import onto a path that has a
    cheaper way to ask.
    """
    if hosted:
        return str(getattr(store, "search")(QUERY, k=SEARCH_K) or "")
    hits = getattr(store, "search")(QUERY, k=SEARCH_K)
    lines = []
    for index, hit in enumerate(hits or [], start=1):
        cid = getattr(hit, "claim_id", None) or getattr(hit, "id", None)
        if not cid:
            continue
        body = " ".join(str(getattr(hit, name, "") or "")
                        for name in ("subject", "predicate", "object")).strip()
        lines.append(f"{index}. [id={cid}] {body}")
    return "\n".join(lines)


def _run_gh(args: "list[str]") -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True,
                          timeout=GH_TIMEOUT_SEC, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def probe(ref: str, run=_run_gh) -> str:
    """`MERGED`, `CLOSED`, `OPEN`, or `""` when GitHub could not be asked.

    The empty string is load-bearing and is not `OPEN`. "We could not check" and "it is
    still open" are the pair that must not look alike: collapsing them caches a
    no-network run as a real answer and the ref is never asked about again.
    """
    owner_repo, _, number = ref.partition("#")
    if not owner_repo or not number.isdigit():
        return ""
    try:
        out = run(["pr", "view", number, "--repo", owner_repo, "--json", "state",
                   "--jq", ".state"])
    except Exception:
        return ""
    state = out.strip().upper()
    return state if state in ("MERGED", "CLOSED", "OPEN") else ""


def refresh(store: object, hosted: bool, now: float, run=_run_gh) -> int:
    """Resolve refs and record candidates. Returns how many are outstanding.

    Called from `capture.py`, which is async, so the cost is off the turn's critical path.
    """
    state = read_state()
    if now - float(state.get("swept_at") or 0) < SWEEP_EVERY_SEC:
        return len(state.get("candidates") or {})

    state["swept_at"] = now
    verdicts = dict(state.get("verdicts") or {})
    candidates = dict(state.get("candidates") or {})

    try:
        rendered = search(store, hosted)
    except Exception as exc:
        # Written down rather than swallowed: a sweep that cannot read the store is not the
        # same event as a store with nothing to close, and only one of them is worth acting on.
        _log(f"search failed: {type(exc).__name__}")
        write_state(state)
        return len(candidates)

    probes = 0
    for claim_id, text in rows(rendered):
        subject = text.split(" ", 1)[0] if text else ""
        for ref in refs(subject, text):
            known = verdicts.get(ref)
            if known not in TERMINAL:
                if known and known != "OPEN":
                    continue
                if probes >= MAX_PROBES:
                    continue
                probes += 1
                verdict = probe(ref, run)
                if not verdict:
                    continue
                verdicts[ref] = verdict
                known = verdict
            if known in TERMINAL and claim_id not in candidates:
                candidates[claim_id] = {"ref": ref, "found_at": now,
                                        "excerpt": text[:160], "shown_at": 0}

    state["verdicts"], state["candidates"] = verdicts, candidates
    write_state(state)
    _log(f"probes={probes} verdicts={len(verdicts)} candidates={len(candidates)}")
    return len(candidates)


def block(now: float, header: str = "") -> str:
    """The lines to put in front of a session, or `""` when there is nothing to say.

    Reading only, and it marks what it shows so the same candidate does not reappear every
    session until it is dealt with.
    """
    state = read_state()
    candidates = state.get("candidates") or {}
    if not candidates:
        return ""

    due = [(cid, item) for cid, item in candidates.items()
           if isinstance(item, dict)
           and now - float(item.get("shown_at") or 0) > REMIND_AFTER_SEC]
    due.sort(key=lambda pair: float(pair[1].get("found_at") or 0))
    due = due[:MAX_SHOWN]
    if not due:
        return ""

    lines = [header or HEADER]
    for cid, item in due:
        lines.append(f"- {cid} names {item.get('ref')}, which has merged: "
                     f"{str(item.get('excerpt') or '').strip()}")
        candidates[cid] = {**item, "shown_at": now}
    state["candidates"] = candidates
    write_state(state)
    return "\n".join(lines)


HEADER = (
    "Memvara — stored defects whose fix has since landed. Each names a pull request that "
    "is now merged, so the claim likely describes something that is no longer true. "
    "Verify against the merged tree first, then close it with memory_end at the merge "
    "instant (it was right when written; memory_forget would say it never was). Nothing "
    "here has been closed for you."
)


def forget_candidate(claim_id: str) -> None:
    """Drop one candidate, for a caller that has just closed it."""
    state = read_state()
    candidates = state.get("candidates") or {}
    if candidates.pop(claim_id, None) is not None:
        state["candidates"] = candidates
        write_state(state)


def have_gh() -> bool:
    """Whether `gh` is on PATH at all. Absence is normal, not a fault."""
    for directory in (os.environ.get("PATH") or "").split(os.pathsep):
        if directory and os.path.exists(os.path.join(directory, "gh")):
            return True
    return False
