"""`/memvara:setup`: the on/off switch for every memvara feature, and the status line.

Usage, as the command runs it:

    python3 setup.py                       list every feature, its value and its default
    python3 setup.py <feature> on|off      set one feature
    python3 setup.py query_rewrite on --yes
                                           turn query rewrite on after seeing its cost
    python3 setup.py verify-key [--yes]    show what query rewrite costs; with --yes,
                                           make one test call and record the result
    python3 setup.py remove-status-line    take memvara's status line out of Claude Code
    python3 setup.py check <feature>       exit 0 when the feature is on, 1 when it is off
    python3 setup.py install-status-line [--hook]

The switches live in `~/.memvara/settings.json`, a flat JSON object of
`feature_name: true|false`, where a missing key means the feature's default. The features
and their defaults are the ones the vendored hooks read, `FEATURE_DEFAULTS` in
`hooks/lib/settings.py`, which is a copy of the library's, so this command, the hooks and
the library never disagree about which names exist or what they default to. A name outside
it is refused.

Query rewrite has a check of its own. The recall hook asks the local store's model to
rewrite each prompt's query only after `verify-key --yes` has made one test call through
the library and the model answered it (`hooks/lib/read_model.py`). `verify-key` without
`--yes` prints what a rewrite adds to each prompt, in time and in model calls, and changes
nothing, so the user sees the cost before anything is switched on. The result of the test
call is saved in the hooks' own state file, `~/.memvara/.hooks/read_model.json`, not in
the switch file, and a record an earlier build left in the switch file is removed once a
new one is saved.

Two features also change Claude Code's own settings file, `~/.claude/settings.json`
(or `$CLAUDE_CONFIG_DIR/settings.json`), because that is where Claude Code reads them:

- `status_line`: the plugin's `SessionStart` hook runs `install-status-line --hook`, which
  adds memvara's status line only when no status line is set at all, and records the exact
  command it wrote in `~/.memvara/.hooks/statusline.json`. A status line counts as
  memvara's only when its command is this plugin's own or the recorded one, so another
  tool's status line is never replaced or changed. `remove-status-line` takes memvara's line out and
  switches `status_line` off, so the next session does not add it back.
- `research_agent`: switching it off adds the deny rule `Agent(memvara:memory-researcher)`
  to `permissions.deny`, which is how Claude Code disables one subagent. Switching it on
  removes that rule and nothing else.

Every write goes to a temporary file in the same directory and is renamed into place, so a
reader never sees half a file. A settings file that is not valid JSON is never
overwritten: the command says so and changes nothing. When the `SessionStart` hook cannot
install the status line, it says so in `~/.memvara/.hooks/setup.log` rather than on screen.
"""

from __future__ import annotations

import difflib
import json
import os
import sys
import time

PLUGIN = os.path.dirname(os.path.abspath(__file__))
STATUSLINE = os.path.join(PLUGIN, "statusline.py")

#: The permission rule that disables the memory-research subagent. Plugin agents are named
#: `<plugin>:<agent>`.
AGENT_RULE = "Agent(memvara:memory-researcher)"

#: What each switch controls, in words a user can act on. A feature the hooks list and this
#: does not describe still prints, with a note that says so; a test requires an entry for
#: every name, so a sync that adds a feature fails until somebody writes one.
DESCRIPTIONS = {
    "index_command": "/memvara:index, which records facts about the current repository. "
                     "Off: the command refuses to run.",
    "research_agent": "The memory-researcher subagent. Off: Claude Code is told not to use "
                      "it, through a deny rule in ~/.claude/settings.json.",
    "project_scope": "The hooks and the MCP server send the repository's git project, so "
                     "memories are kept per repository. The MCP server reads the switch "
                     "when it connects, so a change reaches it in the next session.",
    "status_line": "The hooks count memory activity for the status line. Off: counting "
                   "stops and the status line shows 'off'.",
    "recall_mark": "Every memory line the hooks put into a prompt starts with ⋈.",
    "profile": "The memory_profile tool, one call for standing preferences, recent "
               "memories and buckets.",
    "forget_matching": "The memory_end_matching and memory_forget_matching tools, which "
                       "end or retire every memory matching a query after a preview.",
    "end_reason": "A reason stored when a memory is ended, retired or given an end date.",
    "links": "The memory_link tool and typed links between memories.",
    "documents": "The document tools, which store a whole document so that a search can "
                 "find passages in it: memory_add_document, memory_get_document, "
                 "memory_list_documents and memory_delete_document.",
    "retrieval_chunks": "A stored document is split into passages of about 1,000 "
                        "characters, so a search finds the passage that answers it. Off: "
                        "each document is stored as one piece.",
    "extraction_chunks": "A turn longer than 6,000 characters is read for facts in pieces "
                         "of at most 6,000 characters. It is off by default because it has "
                         "not met its release bar yet: the one measured run found 4 of 5 "
                         "key facts in a long turn.",
    "ingest_urls": "A document can be added from a web address, which the server fetches. "
                   "Off: a document has to be sent as text or as a file.",
    "ingest_media": "A document can be an image, an audio file or a video, which the "
                    "server's model turns into text. Off: those types are refused.",
    "query_rewrite": "Before a search, a model writes up to three other phrasings of the "
                     "question and reads any dates out of it, and every phrasing is "
                     "searched. The recall hook uses it only after /memvara:setup "
                     "verify-key has checked your model key.",
    "synthesis": "memory_recall can put a short summary, written by a model, above the "
                 "recalled notes when the caller asks for one. The recall hook never asks "
                 "for one.",
    "metadata_filters": "memory_search and memory_recall can keep only the memories whose "
                        "metadata matches given values, or that came from a document whose "
                        "file path starts with given text. Off: a search that asks for "
                        "either is refused, never answered unfiltered.",
    "encryption": "A new local store is encrypted on disk, vectors included. The key is "
                  "looked up in the OS keychain, then in MEMVARA_DB_KEY, then in "
                  "~/.memvara/db.key, where one is created when a new store needs it. "
                  "Losing the key makes the store unreadable, so back it up with "
                  "memvara encrypt --export-key. An existing store stays as it is; "
                  "memvara encrypt converts one.",
}

#: What each phase 2 switch costs when it is on, in words a user can act on. A test
#: requires an entry for every one of them.
COSTS = {
    "documents": "No model call of its own. Each passage of a document is read for facts "
                 "like a turn, which calls your extraction model if one is configured.",
    "retrieval_chunks": "No model call. One stored row and one embedding per passage, "
                        "instead of one per document.",
    "extraction_chunks": "One extraction call to your model per piece, instead of one per "
                         "long turn.",
    "ingest_urls": "No model call. One web request per address added, of at most 10 MB "
                   "and 20 seconds.",
    "ingest_media": "One call to your model per image, audio file or video, on your key. "
                    "The anthropic backend reads images only; the openai backend also "
                    "transcribes audio and the sound of a video.",
    "query_rewrite": "One chat call to your model per search that rewrites. For the recall "
                     "hook that is one call per prompt, and it adds time to each prompt. "
                     "/memvara:setup verify-key shows both before anything is turned on.",
    "synthesis": "One chat call to your model per recall that asks for a summary.",
    "metadata_filters": "No model call. The filter runs inside the store, before the "
                        "number of results is cut, so a filtered search still returns as "
                        "many matches as exist.",
    "encryption": "Under 1 ms more per write, and more memory from the first search, when "
                  "the vectors are decrypted into memory instead of read from disk. "
                  "Measured on a store of 20,000 claims: 0.53 ms against 0.30 ms per write, "
                  "and 122 MB against 71 MB of peak memory. It needs "
                  "pip install 'memvara[encrypt]'.",
}

#: Features the server provides rather than the plugin. The switch is saved like any other,
#: but no server reads this file yet, and saying otherwise would be a promise nothing keeps.
SERVER_SIDE = frozenset({"profile", "forget_matching", "end_reason", "links", "documents",
                         "retrieval_chunks", "extraction_chunks", "ingest_urls",
                         "ingest_media", "synthesis", "metadata_filters",
                         "encryption"})

SERVER_NOTE = ("saved, but no server reads this file yet: the hosted server's switches are "
               "set by its deployment, and a local memvara-mcp server takes "
               "MEMVARA_FEATURE_{upper}=0 or =1")

#: `query_rewrite` is read by the recall hook from this file, and by a server from its own
#: environment, so it gets its own note.
REWRITE_NOTE = ("The recall hook reads this switch from this file. A local memvara-mcp "
                "server reads MEMVARA_FEATURE_QUERY_REWRITE=0 or =1 instead, and the "
                "hosted server's switches are set by its deployment")

#: The measured local overhead of a rewritten read, from memvara/memvara#231. The model
#: call is not in these numbers.
REWRITE_MEASURED = ("Measured without the model call, a read with query rewrite took a "
                    "median of 21.0 ms against 5.6 ms without it (200 reads, k=10, a local "
                    "store of 1,000 claims and 1,000 turns, and a model stand-in that "
                    "answers instantly).")

#: Where each backend's prices are published. memvara keeps no price list, so setup never
#: states a price; it says where to find one.
PRICES = {
    "anthropic": "https://www.anthropic.com/pricing",
    "openai": "https://openai.com/api/pricing (or the price list of the server "
              "OPENAI_BASE_URL points to, if you set one)",
}


def _settings_module():
    from memvara_hooks import hooks_lib

    return hooks_lib("settings")[0]


def _read_model_module():
    from memvara_hooks import hooks_lib

    # `host=True`: `lib.read_model` reads the client's configuration through `lib.ipc`,
    # which needs the hooks' `core` and `hosts` packages.
    return hooks_lib("read_model", host=True)[0]


def features() -> "tuple[str, ...]":
    return tuple(_settings_module().FEATURES)


def memvara_settings_path() -> str:
    return _settings_module().SETTINGS


def hooks_state_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".memvara", ".hooks")


def claude_settings_path() -> str:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude")
    return os.path.join(base, "settings.json")


def _home_relative(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def load(path: str) -> "tuple[dict | None, str | None]":
    """`(data, None)`, `({}, None)` for a missing file, or `(None, reason)` when unusable."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError) as exc:
        return None, f"{_home_relative(path)} could not be read as JSON ({exc})"
    if not isinstance(data, dict):
        return None, f"{_home_relative(path)} does not hold a JSON object"
    return data, None


def write(path: str, data: dict) -> None:
    """Write `data` as indented JSON through a temporary file and a rename.

    An existing file keeps its permission bits. Raises `OSError` on failure, and leaves no
    temporary file behind. The hooks' `lib/state_file.py` has an atomic writer too, but it
    writes compact JSON at mode 0600 and is vendored, so it cannot be changed here; a
    settings file a person edits by hand keeps its layout and its mode.
    """
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    try:
        mode = os.stat(path).st_mode & 0o777
    except FileNotFoundError:
        mode = 0o644
    tmp = os.path.join(directory, f".{os.path.basename(path)}.memvara-{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def log(line: str) -> None:
    """Append one line to `~/.memvara/.hooks/setup.log`. Silent on failure."""
    try:
        os.makedirs(hooks_state_dir(), exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(os.path.join(hooks_state_dir(), "setup.log"), "a",
                  encoding="utf-8") as fh:
            fh.write(f"{stamp} {line}\n")
    except OSError:
        pass


# -- the status line ----------------------------------------------------------------------

def status_line_entry() -> dict:
    """The `statusLine` value memvara writes.

    `-S` skips the `site` module, which the status line does not need and which costs
    several milliseconds more on a Python with many packages installed. The script uses
    only the standard library and the vendored hooks, and a test checks that its output is
    the same bytes with and without `-S`.
    """
    return {"type": "command", "command": f'python3 -S "{STATUSLINE}"'}


def _record_path() -> str:
    return os.path.join(hooks_state_dir(), "statusline.json")


def _recorded_command() -> "str | None":
    data, _ = load(_record_path())
    command = (data or {}).get("command")
    return command if isinstance(command, str) else None


def _record(command: "str | None") -> None:
    """Remember the command memvara wrote, or forget it when `command` is `None`."""
    if command is None:
        try:
            os.unlink(_record_path())
        except OSError:
            pass
    else:
        write(_record_path(), {"command": command})


def is_ours(entry: object) -> bool:
    """Whether a `statusLine` value is one memvara wrote.

    Only two commands count: the one this copy of the plugin would write, and the one
    memvara recorded when it last installed its line, which is how the line of an earlier
    plugin version is recognised after an update moves the plugin's directory. A command
    that merely looks similar, such as another tool's `statusline.py` in a directory whose
    name contains memvara, is another tool's line and is left alone.
    """
    if not isinstance(entry, dict) or entry.get("type") != "command":
        return False
    command = entry.get("command")
    return command in (status_line_entry()["command"], _recorded_command())


def install_status_line(check_switch: bool = True) -> "tuple[str, str]":
    """Add memvara's status line when there is none. Returns `(outcome, sentence)`.

    Outcomes: `installed`, `updated` (memvara's line pointed at an older plugin directory),
    `present`, `other` (another tool's line, untouched), `off` (the switch is off) and
    `error` (the settings file is unusable, untouched).
    """
    path = claude_settings_path()
    where = _home_relative(path)
    if check_switch and not _settings_module().enabled("status_line"):
        return "off", ("The status_line switch is off, so memvara's status line was not "
                       "added.")
    data, problem = load(path)
    if data is None:
        return "error", f"Nothing changed: {problem}."
    current = data.get("statusLine")
    wanted = status_line_entry()
    if current is None:
        data["statusLine"] = wanted
        write(path, data)
        _record(wanted["command"])
        return "installed", (f"memvara added its status line to {where}. Run "
                             "/memvara:setup remove-status-line to take it out.")
    if is_ours(current):
        if current.get("command") == wanted["command"]:
            return "present", f"memvara's status line is already set in {where}."
        data["statusLine"] = {**current, "command": wanted["command"]}
        write(path, data)
        _record(wanted["command"])
        return "updated", (f"memvara's status line in {where} now runs this version of "
                           "the plugin.")
    return "other", (f"{where} already has a status line from another tool, so memvara "
                     "left it as it is and did not add its own.")


def remove_status_line() -> "tuple[str, str]":
    """Take memvara's status line out. Another tool's line is never touched."""
    path = claude_settings_path()
    where = _home_relative(path)
    data, problem = load(path)
    if data is None:
        return "error", f"Nothing changed: {problem}."
    current = data.get("statusLine")
    if current is None:
        return "absent", f"{where} has no status line, so there was nothing to remove."
    if not is_ours(current):
        return "other", (f"The status line in {where} belongs to another tool, so memvara "
                         "left it as it is.")
    del data["statusLine"]
    write(path, data)
    _record(None)
    return "removed", f"memvara's status line was removed from {where}."


# -- the research agent -------------------------------------------------------------------

def plan_agent_rule(denied: bool) -> "tuple[str, str, dict | None]":
    """Work out the settings change for `AGENT_RULE` without writing anything.

    Returns `(outcome, sentence, data)`, where `data` is the whole new settings object to
    write, or `None` when there is nothing to write (`unchanged`) or it cannot be done
    (`error`). Only `permissions.deny` changes; every other key is kept.
    """
    path = claude_settings_path()
    where = _home_relative(path)
    data, problem = load(path)
    if data is None:
        return "error", f"Nothing changed: {problem}.", None
    permissions = data.get("permissions", {})
    if not isinstance(permissions, dict):
        return "error", (f"Nothing changed: permissions in {where} is not an object, so "
                         "memvara did not edit it."), None
    deny = permissions.get("deny", [])
    if not isinstance(deny, list):
        return "error", (f"Nothing changed: permissions.deny in {where} is not a list, so "
                         "memvara did not edit it."), None
    if denied == (AGENT_RULE in deny):
        state = "denied" if denied else "allowed"
        return ("unchanged", f"The memory-researcher agent was already {state} in {where}.",
                None)
    deny = [*deny, AGENT_RULE] if denied else [rule for rule in deny if rule != AGENT_RULE]
    permissions = dict(permissions)
    if deny:
        permissions["deny"] = deny
    else:
        permissions.pop("deny", None)
    data = dict(data)
    if permissions:
        data["permissions"] = permissions
    else:
        data.pop("permissions", None)
    if denied:
        return "denied", f"Added {AGENT_RULE} to permissions.deny in {where}.", data
    return "allowed", f"Removed {AGENT_RULE} from permissions.deny in {where}.", data


def agent_rule_state() -> str:
    """`present`, `absent`, or the reason the settings file could not be read."""
    data, problem = load(claude_settings_path())
    if data is None:
        return f"unknown, because {problem}"
    permissions = data.get("permissions")
    deny = permissions.get("deny") if isinstance(permissions, dict) else None
    return "present" if isinstance(deny, list) and AGENT_RULE in deny else "absent"


# -- query rewrite and its key check ------------------------------------------------------

def _model_words(backend: str, model_setting: str, model: str = "") -> str:
    """The configured model, named the way a user would look it up."""
    name = model or model_setting
    if name:
        return f"the model {name} (the {backend} backend)"
    return f"the {backend} backend's default model"


def rewrite_cost() -> str:
    """What query rewrite adds to each prompt, in time and in model calls. Changes nothing.

    No price is stated, because memvara keeps no price list: the text names the model and
    says where its price is published.
    """
    backend, model_setting = _read_model_module().configured()
    lines = ["Query rewrite on the recall hook", ""]
    if backend == "none":
        lines += [
            "No model is configured for a local store: MEMVARA_LLM is not set in the "
            "memvara MCP server's configuration, or it is none. Query rewrite needs one, so "
            "there is nothing to check and the recall hook reads without it.",
            "",
            "On a hosted install, the hosted service would rewrite with your "
            "organisation's own key, which this machine cannot check, so the recall hook "
            "always asks it for a plain read."]
        return "\n".join(lines)
    who = _model_words(backend, model_setting)
    where = PRICES.get(backend, "your provider's pricing page")
    lines += [
        f"What it does: before each prompt's search, the recall hook sends your prompt and "
        f"today's date to {who}. The model answers with up to three other phrasings and "
        "any dates the prompt names, and every phrasing is searched.",
        "",
        f"Time: {REWRITE_MEASURED} The model call adds its own time on top of that. The "
        "library stops waiting for it after 10 seconds, and the recall hook after 5 "
        "seconds, and then uses the result without a rewrite, so a slow or failing model "
        "never costs you your memories.",
        "",
        f"Cost: one chat call per prompt to {who}, on your own key, with a reply of at "
        "most 300 tokens. memvara does not know what that model costs. Look up its price "
        f"per token at {where}."]
    return "\n".join(lines)


def rewrite_state() -> str:
    """One sentence saying whether the recall hook rewrites its query now, and why."""
    read_model = _read_model_module()
    if not _settings_module().enabled("query_rewrite"):
        return "The recall hook does not rewrite queries, because query_rewrite is off."
    record = read_model.recorded()
    if not isinstance(record, dict):
        return ("The recall hook does not rewrite queries yet: no model key has been "
                "checked. Run /memvara:setup verify-key to see what it costs.")
    when = str(record.get("checked_at") or "")[:10] or "an unknown date"
    if record.get("outcome") != "applied":
        return (f"The recall hook does not rewrite queries: the check on {when} found "
                f"{record.get('outcome') or 'nothing'}. Run /memvara:setup verify-key to "
                "check again.")
    if not read_model.verified_for_current_config():
        return (f"The recall hook does not rewrite queries: the model configured now is "
                f"not the one checked on {when}. Run /memvara:setup verify-key to check "
                "it.")
    who = _model_words(str(record.get("backend")), str(record.get("model_setting") or ""),
                       str(record.get("model") or ""))
    return (f"The recall hook rewrites each prompt's query with {who}, checked on {when}. "
            "That is one model call per prompt.")


#: What each outcome of the test call means, and what to do about it.
_VERIFIED = {
    "applied": "The test call worked: the model answered.",
    "key_rejected": "The provider refused the key{status}. Fix the key in the memvara MCP "
                    "server's configuration, then run /memvara:setup verify-key --yes "
                    "again.",
    "fallback": "The test call failed ({reason}). Nothing is wrong with the setting "
                "itself; run /memvara:setup verify-key --yes again to retry.",
    "unconfigured": "The local store has no model that can chat, so there is nothing to "
                    "rewrite with. Set MEMVARA_LLM in the memvara MCP server's "
                    "configuration to use one.",
    "disabled": "The local store was built with query rewrite off: "
                "MEMVARA_FEATURE_QUERY_REWRITE=0 is set for it. Remove that setting to use "
                "it.",
    "no_local_store": "There is no local store to check. On a hosted install, the hosted "
                      "service would rewrite with your organisation's own key, which this "
                      "machine cannot check, so the recall hook always asks it for a plain "
                      "read.",
    "unsupported": "The installed memvara library is older than query rewrite. Upgrade it "
                   "with pip install -U memvara, then run /memvara:setup verify-key --yes "
                   "again.",
    "error": "The check could not run ({reason}). Run /memvara:setup verify-key --yes "
             "again, and look at the memvara MCP server's configuration if it happens "
             "twice.",
}


def verify_key(confirmed: bool) -> "tuple[int, str]":
    """Show the cost of query rewrite, or, when `confirmed`, check the key and record it.

    Unconfirmed, nothing is called and nothing is written: exit 0. Confirmed, one test
    rewrite goes through the library (`read_model.check()`) and its result is saved in the
    hooks' state file `~/.memvara/.hooks/read_model.json` (`read_model.save()`), whatever
    it was. Exit 0 means the model answered and the recall hook will rewrite; exit 1 means
    it will not, or the result could not be saved.
    """
    if not confirmed:
        cost = rewrite_cost()
        if _read_model_module().configured()[0] == "none":
            return 0, cost
        return 0, (f"{cost}\n\n{rewrite_state()}\n\nTo make one test call to that model "
                   "now, and let the recall hook rewrite each prompt's query if it "
                   "answers, run: /memvara:setup verify-key --yes")
    read_model = _read_model_module()
    record = read_model.check()
    where = _home_relative(read_model.STATE)
    if not read_model.save(record):
        return 1, (f"The test call ran, but its result could not be saved to {where}, so "
                   "the recall hook will not rewrite. Check that the directory can be "
                   "written, then run /memvara:setup verify-key --yes again.")
    _forget_old_record(read_model.KEY)
    outcome = str(record.get("outcome") or "")
    status = f" (HTTP {record['status']})" if record.get("status") else ""
    said = [_VERIFIED.get(outcome, "The check returned {outcome}.").format(
        status=status, reason=record.get("reason") or "no reason given", outcome=outcome)]
    said.append(f"The result is saved in {where}.")
    said.append(rewrite_state())
    return (0 if outcome == "applied" else 1), " ".join(said)


def _forget_old_record(key: str) -> None:
    """Take an earlier build's check record out of the switch file, once one is saved.

    It lived under `key` in `~/.memvara/settings.json`. The hooks read it from there only
    while the state file is missing, so once a check is saved it is dead weight in a file
    of switches. A settings file that cannot be read is left exactly as it is.
    """
    path = memvara_settings_path()
    data, _ = load(path)
    if data and key in data:
        del data[key]
        write(path, data)
        _settings_module().reload()


# -- the switches -------------------------------------------------------------------------

def _override(name: str) -> "str | None":
    """The environment variable that overrides the file for `name`, when one does."""
    variable = f"MEMVARA_FEATURE_{name.upper()}"
    raw = os.environ.get(variable)
    if raw is not None and raw.strip().lower() in ("1", "true", "on", "yes",
                                                    "0", "false", "off", "no"):
        return variable
    return None


def refuse_unknown(name: str) -> "str | None":
    """A sentence refusing `name`, or `None` when it is a feature."""
    known = features()
    if name in known:
        return None
    close = difflib.get_close_matches(name, known, n=1)
    hint = f" (did you mean {close[0]}?)" if close else ""
    return f"{name!r}{hint} is not a memvara feature. The features are {', '.join(known)}."


def listing() -> str:
    settings = _settings_module()
    names = tuple(settings.FEATURES)
    stored, problem = load(settings.SETTINGS)
    lines = [f"memvara features, stored in {_home_relative(settings.SETTINGS)}. "
             "A feature that is not set has its default.", ""]
    if problem:
        lines += [f"Note: {problem}. Every feature reads as its default until it is fixed.",
                  ""]
    width = max(len(name) for name in names)
    indent = f"  {' ' * width}  "
    for name in names:
        value = "on" if settings.enabled(name) else "off"
        default = "on" if settings.FEATURE_DEFAULTS[name] else "off"
        where = []
        if isinstance((stored or {}).get(name), bool):
            where.append("set in the file")
        variable = _override(name)
        if variable:
            where.append(f"{variable} overrides the file")
        suffix = f"  ({'; '.join(where)})" if where else ""
        lines.append(f"  {name.ljust(width)}  {value.ljust(3)}  default {default}{suffix}")
        lines.append(indent + DESCRIPTIONS.get(name, "No description yet."))
        if name in COSTS:
            lines.append(f"{indent}Cost: {COSTS[name]}")
        if name in SERVER_SIDE:
            lines.append(f"{indent}This switch is "
                         + SERVER_NOTE.format(upper=name.upper()) + ".")
        if name == "query_rewrite":
            lines.append(f"{indent}{REWRITE_NOTE}.")
            lines.append(indent + rewrite_state())
    path = claude_settings_path()
    where = _home_relative(path)
    data, unreadable = load(path)
    current = (data or {}).get("statusLine")
    if data is None:
        state = f"unknown, because {unreadable}"
    elif current is None:
        state = f"not installed; {where} has no status line"
    elif is_ours(current):
        state = f"installed in {where}"
    else:
        state = f"not installed; {where} has another tool's status line"
    lines += ["", f"Status line: {state}.",
              f"Research agent rule {AGENT_RULE}: {agent_rule_state()}.", "",
              "Change one with: /memvara:setup <feature> on|off"]
    return "\n".join(lines)


def set_feature(name: str, value: bool) -> "tuple[int, str]":
    """Set one switch. Exit code 1 means neither settings file was changed."""
    problem = refuse_unknown(name)
    if problem:
        return 2, problem
    path = memvara_settings_path()
    data, unreadable = load(path)
    if data is None:
        return 1, f"Nothing changed: {unreadable}. Fix or delete it, then try again."
    # The client's settings change is worked out before anything is written, so a file
    # that cannot take it stops the whole change rather than leaving the switch saved and
    # the rule missing.
    rule = None
    if name == "research_agent":
        outcome, sentence, planned = plan_agent_rule(denied=not value)
        if outcome == "error":
            return 1, f"{sentence} The research_agent switch was not changed either."
        rule = (sentence, planned)
    previous = dict(data)
    existed = os.path.exists(path)
    data[name] = value
    write(path, data)
    said = [f"{name} is now {'on' if value else 'off'} in {_home_relative(path)}."]
    if rule is not None:
        sentence, planned = rule
        if planned is not None:
            try:
                write(claude_settings_path(), planned)
            except OSError as exc:
                # Put the switch back, so exit 1 still means nothing changed.
                if existed:
                    write(path, previous)
                else:
                    os.unlink(path)
                return 1, (f"Nothing changed: {_home_relative(claude_settings_path())} "
                           f"could not be written ({exc}).")
        said.append(sentence)
    variable = _override(name)
    if variable:
        said.append(f"{variable} is set in this environment and overrides the file until "
                    "it is unset.")
    if name == "status_line" and value:
        said.append(install_status_line(check_switch=False)[1])
    if name == "status_line" and not value:
        said.append("The status line stays and shows 'off'. Run /memvara:setup "
                    "remove-status-line to take it out.")
    if name == "project_scope":
        said.append("Hooks use it from the next prompt; the MCP server from the next "
                    "session.")
    if name in SERVER_SIDE:
        said.append("This switch is " + SERVER_NOTE.format(upper=name.upper()) + ".")
    if name == "query_rewrite":
        # This process read the file before writing it; report what the hooks will read.
        _settings_module().reload()
        said.append(REWRITE_NOTE + ".")
        said.append(rewrite_state())
    return 0, " ".join(said)


def rewrite_would_start() -> bool:
    """Whether turning `query_rewrite` on would make the recall hook start rewriting now.

    True when the switch is off and a key check for the configured model is on record, so
    the only thing between the user and one model call per prompt is this switch. Whether
    the key is checked for this model is `read_model`'s rule, asked here rather than copied.
    """
    return (not _settings_module().enabled("query_rewrite")
            and _read_model_module().verified_for_current_config())


def main(argv: "list[str]") -> int:
    if not argv or argv == ["list"]:
        print(listing())
        return 0
    command = argv[0]
    if command == "install-status-line":
        hook = "--hook" in argv[1:]
        try:
            outcome, sentence = install_status_line()
        except Exception as exc:  # noqa: BLE001 -- a SessionStart hook must never fail
            if not hook:
                print(f"Could not install the status line: {exc}", file=sys.stderr)
                return 1
            log(f"install-status-line failed: {type(exc).__name__}: {exc}")
            return 0
        if hook:
            # A SessionStart hook's systemMessage is the one line the person at the
            # terminal sees. Only a change is worth that line; a failure goes to the log,
            # so "could not" and "did not need to" do not look alike.
            if outcome in ("installed", "updated"):
                print(json.dumps({"systemMessage": sentence}))
            elif outcome == "error":
                log(f"install-status-line: {sentence}")
            return 0
        print(sentence)
        return 1 if outcome == "error" else 0
    if command == "remove-status-line":
        outcome, sentence = remove_status_line()
        if outcome == "error":
            print(sentence)
            return 1
        code, switched = set_feature("status_line", False)
        if code:
            print(f"{sentence} {switched}")
        else:
            print(f"{sentence} status_line is now off, so the next session does not add "
                  "it back.")
        return code
    if command == "check" and len(argv) == 2:
        problem = refuse_unknown(argv[1])
        if problem:
            print(problem)
            return 2
        if _settings_module().enabled(argv[1]):
            return 0
        print(f"The {argv[1]} feature is switched off. Turn it on with "
              f"/memvara:setup {argv[1]} on.")
        return 1
    if command == "verify-key" and argv[1:] in ([], ["--yes"]):
        code, sentence = verify_key(confirmed=argv[1:] == ["--yes"])
        print(sentence)
        return code
    confirmed = argv[2:] == ["--yes"] and command == "query_rewrite"
    if (len(argv) == 2 or confirmed) and argv[1].lower() in ("on", "off"):
        value = argv[1].lower() == "on"
        if command == "query_rewrite" and value and not confirmed and rewrite_would_start():
            # The key was checked while the switch was off, so this switch alone starts
            # one model call per prompt. The user sees what that costs first.
            print(f"{rewrite_cost()}\n\nNothing changed yet. To turn query rewrite on for "
                  "the recall hook, run: /memvara:setup query_rewrite on --yes")
            return 0
        code, sentence = set_feature(command, value)
        print(sentence)
        return code
    problem = refuse_unknown(command) if len(argv) <= 2 else None
    print(problem or "usage: /memvara:setup [<feature> on|off | verify-key [--yes] | "
                     "remove-status-line]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
