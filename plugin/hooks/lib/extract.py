"""Turn transcript text into triples using the user's own Claude Code login.

The obvious way to extract facts is an API key and a direct call to a model. This
deliberately does not do that, because the key is a second bill for a model the user is
already paying for. `claude -p` runs headless against the login they already have.

What it costs instead is overhead. A headless run boots a whole Claude Code session, so
roughly 21k tokens of *its* system prompt are read before a word of the transcript is —
measured at 16.3k cache-read plus 4.9k cache-creation on a two-sentence input, 12.2s,
about $0.018 on Haiku. Per turn that is indefensible; the caller amortises it by batching,
and this module is written to be called rarely with a lot of text rather than often with a
little.

Two guards matter more than the cost:

* **Recursion.** A `Stop` hook that spawns Claude gives that child a `Stop` hook too. The
  child is launched with an empty hook set, and the environment sentinel is a second
  independent stop in case a future client reads hooks from somewhere this does not
  override.
* **Silence.** Every failure here returns no facts. A capture that cannot run is a capture
  that did not happen, never an error the user has to see.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

from .usage import record_extraction

#: Set in the child's environment. If a hook ever sees this, it is running underneath an
#: extraction and must not start another one.
SENTINEL = "MEMVARA_CAPTURE_ACTIVE"

#: Cheapest model that reliably returns well-formed triples for this job.
MODEL = "claude-haiku-4-5-20251001"

#: Generous: the measured call was 12.2s, and a batched span is larger. The `Stop` hook
#: entry in hooks.json must allow more than this or the kill lands in the wrong place.
TIMEOUT_SEC = 90

PROMPT = """\
Extract durable facts about the user from the conversation below.

Return JSON only, no prose, in exactly this shape:
{"facts": [{"subject": "user", "predicate": "snake_case_relation", "object": "short value"}]}

Rules:
- Only facts that would still matter next week. Skip anything about this conversation.
- `object` is the value alone: "Lisbon", not "they live in Lisbon".
- `subject` is "user" unless the fact is plainly about a named system or third party.
- Prefer few, high-confidence facts. An empty list is a correct answer.

Conversation:
"""


def _payload(text: str) -> "tuple[str, dict]":
    """The model's reply and what it cost, or `('', {})` if the run failed.

    Cost is returned rather than discarded because here is the only place it exists.
    `--output-format json` puts usage on the envelope beside `result`; reading `result`
    alone, as this first did, throws the token counts away with the process.
    """
    if os.environ.get(SENTINEL):
        return "", {}

    env = dict(os.environ)
    env[SENTINEL] = "1"

    try:
        proc = subprocess.run(
            [
                "claude", "-p",
                # An empty hook set is what stops this recursing. Without it the child's
                # own Stop hook fires and spawns another child, without limit.
                "--settings", '{"hooks":{}}',
                "--model", MODEL,
                "--output-format", "json",
                PROMPT + text,
            ],
            capture_output=True, text=True, timeout=TIMEOUT_SEC, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return "", {}
    if proc.returncode != 0:
        return "", {}

    try:
        body = json.loads(proc.stdout)
    except ValueError:
        return "", {}

    usage = body.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    if body.get("is_error"):
        # Reported even so. A failed run still burned the preamble, and accounting that
        # counted only successes would make the expensive failures the invisible ones.
        return "", usage
    return str(body.get("result") or ""), usage


def _facts(result: str) -> "list[dict]":
    """Parse the reply, tolerating the code fence the model usually adds."""
    if not result:
        return []
    fenced = re.search(r"```(?:json)?\s*(.*?)```", result, re.S)
    raw = fenced.group(1) if fenced else result
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        body = json.loads(raw[start:end + 1])
    except ValueError:
        return []
    facts = body.get("facts")
    return facts if isinstance(facts, list) else []


def triples(text: str) -> "list[tuple[str, str, str]]":
    """`(subject, predicate, object)` for everything worth storing in `text`.

    Predicates are normalised to snake_case here rather than trusted from the model: an
    unregistered predicate is many-valued forever in this store, so two spellings of one
    relation never reconcile and both keep answering.
    """
    result, usage = _payload(text)
    if usage:
        # Recorded before the reply is even parsed: the tokens were spent whether or not
        # the model returned anything usable.
        record_extraction(usage, model=MODEL)

    out: "list[tuple[str, str, str]]" = []
    for fact in _facts(result):
        if not isinstance(fact, dict):
            continue
        subject = str(fact.get("subject") or "user").strip() or "user"
        predicate = re.sub(r"[^a-z0-9]+", "_", str(fact.get("predicate") or "").lower()).strip("_")
        obj = str(fact.get("object") or "").strip()
        if predicate and obj:
            out.append((subject, predicate, obj))
    return out
