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
SKILL = PLUGIN / "skills" / "memvara"
HOSTED = "https://app.memvara.dev/mcp"
REPO_NAME = "memvara/claude-memvara"

ALLOWED_PLUGIN_FILES = {
    pathlib.Path(".claude-plugin") / "plugin.json",
    pathlib.Path(".mcp.json"),
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

    def test_matches_library_at_lock_sha(self) -> None:
        lock = _lock()
        self.assertEqual(lock["repo"], "memvara/memvara")
        self.assertEqual(lock["path"], "memvara/skills/memvara")
        sha = lock["sha"]
        self.assertEqual(len(sha), 40)
        for rel in ("SKILL.md", "references/hosted-mcp.md"):
            expected = _library_bytes(sha, f"memvara/skills/memvara/{rel}")
            got = (SKILL / rel).read_bytes()
            self.assertEqual(got, expected, f"{rel} drifted from {sha}")


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

    def test_no_hooks_or_app_json(self) -> None:
        self.assertFalse((PLUGIN / "hooks").exists())
        self.assertFalse((PLUGIN / ".app.json").exists())
        self.assertFalse((PLUGIN / "commands").exists())

    def test_plugin_tree_has_no_stray_files(self) -> None:
        allowed = set(ALLOWED_PLUGIN_FILES)
        for path in SKILL.rglob("*"):
            if path.is_file():
                allowed.add(path.relative_to(PLUGIN))
        found = {p.relative_to(PLUGIN) for p in PLUGIN.rglob("*") if p.is_file()}
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
