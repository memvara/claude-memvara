"""Gates for the Claude Code marketplace plugin.

Every file the client will read is asserted here. Markdown is not exempt:
a wrong URL or an npx block is how this repo goes wrong.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
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
        self.assertEqual(body["version"], "0.1.0")
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
        self.assertLessEqual({"UserPromptSubmit", "SessionStart", "Stop"},
                             set(body["hooks"]))

    def test_hooks_are_silent_and_succeed_with_nothing_configured(self) -> None:
        payload = json.dumps({"prompt": "hello", "transcript_path": "/nonexistent"})
        for script in ("recall.py", "session_start.py", "capture.py"):
            with self.subTest(script=script):
                proc = subprocess.run(
                    ["python3", str(HOOKS / script)],
                    input=payload, capture_output=True, text=True,
                    env=self.BARREN, timeout=30,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, "", "a hook with no store must print nothing")

    def test_hooks_do_not_hardcode_a_store_path(self) -> None:
        # Configuration is discovered from the client's own server block. A literal path
        # here would silently read a different store than the MCP server writes.
        for path in HOOKS.rglob("*.py"):
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("MEMVARA_DB=", raw, path)
            self.assertNotIn("/.memvara/workstation", raw, path)


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
