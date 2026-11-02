#!/usr/bin/env python3
"""Unit tests for scripts/checks.py. Run: python3 scripts/test_checks.py"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("checks.py")
SPEC = importlib.util.spec_from_file_location("checks", MODULE_PATH)
checks = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(checks)


class ParseVerdictTest(unittest.TestCase):
    def test_raw_json(self):
        raw = json.dumps(
            {"verdict": "FAIL", "reason": "Missing tests", "suggestions": []}
        )
        parsed = checks.parse_verdict(raw)
        self.assertEqual(parsed["verdict"], "FAIL")
        self.assertEqual(parsed["reason"], "Missing tests")

    def test_claude_envelope_with_fences(self):
        inner = (
            "```json\n"
            '{"verdict":"PASS","reason":"Looks good","suggestions":[]}\n'
            "```"
        )
        raw = json.dumps({"result": inner, "type": "result"})
        parsed = checks.parse_verdict(raw)
        self.assertEqual(parsed["verdict"], "PASS")
        self.assertEqual(parsed["suggestions"], [])

    def test_nextcli_wrapped_response(self):
        inner = '{"verdict":"FAIL","reason":"Secret in source","suggestions":[{"file":"a.ts","line":4,"replacement":"const key = process.env.KEY","comment":"Move it"}]}'
        raw = json.dumps(
            {
                "response": inner,
                "status": "success",
                "note": "wrapped",
            }
        )
        parsed = checks.parse_verdict(raw)
        self.assertEqual(parsed["suggestions"][0]["line"], 4)
        self.assertEqual(parsed["suggestions"][0]["start_line"], 4)

    def test_opencode_events(self):
        body = json.dumps(
            {
                "verdict": "FAIL",
                "reason": "Drop column",
                "suggestions": [
                    {
                        "file": "db.sql",
                        "start_line": 2,
                        "line": 3,
                        "replacement": "-- split\n",
                        "comment": "Split the migration",
                    }
                ],
            }
        )
        raw = "\n".join(
            [
                json.dumps({"type": "text", "part": {"text": "thinking"}}),
                json.dumps({"type": "text", "part": {"text": body}}),
            ]
        )
        parsed = checks.parse_verdict(raw)
        self.assertEqual(parsed["suggestions"][0]["start_line"], 2)
        self.assertEqual(parsed["suggestions"][0]["line"], 3)

    def test_pass_drops_suggestions(self):
        raw = json.dumps(
            {
                "verdict": "PASS",
                "reason": "ok",
                "suggestions": [
                    {
                        "file": "a.ts",
                        "line": 1,
                        "replacement": "x",
                        "comment": "nope",
                    }
                ],
            }
        )
        parsed = checks.parse_verdict(raw)
        self.assertEqual(parsed["suggestions"], [])

    def test_invalid_returns_none(self):
        self.assertIsNone(checks.parse_verdict("not json at all"))

    def test_last_valid_object_wins(self):
        raw = (
            '{"verdict":"FAIL","reason":"first","suggestions":[]}\n'
            'notes\n{"verdict":"PASS","reason":"second","suggestions":[]}'
        )
        parsed = checks.parse_verdict(raw)
        self.assertEqual(parsed["reason"], "second")


class FrontmatterTest(unittest.TestCase):
    def test_split_openrouter_model_id(self):
        provider, model = checks.split_model(
            "openrouter/anthropic/claude-sonnet-4-5", "openai"
        )
        self.assertEqual(provider, "openrouter")
        self.assertEqual(model, "anthropic/claude-sonnet-4-5")

    def test_model_id_without_known_provider_keeps_default(self):
        provider, model = checks.split_model(
            "anthropic/claude-sonnet-4-5", "openrouter"
        )
        self.assertEqual(provider, "anthropic")
        self.assertEqual(model, "claude-sonnet-4-5")

    def test_paths_skip_and_match(self):
        frontmatter = {"paths": ["**/migrations/**", "**/*.sql"]}
        self.assertFalse(
            checks.check_applies(frontmatter, ["src/app.ts", "README.md"])
        )
        self.assertTrue(
            checks.check_applies(frontmatter, ["db/migrations/001_init.sql"])
        )
        self.assertTrue(checks.check_applies(frontmatter, None))
        self.assertTrue(checks.check_applies({}, ["src/app.ts"]))

    def test_parse_paths_list(self):
        text = """---
name: Migration Safety
description: Flag destructive database migrations
model: deepseek/deepseek-v4-pro
paths:
  - "**/migrations/**"
  - "**/*.sql"
---

Body here.
"""
        data, body = checks.parse_frontmatter(text)
        self.assertEqual(data["paths"], ["**/migrations/**", "**/*.sql"])
        self.assertEqual(data["model"], "deepseek/deepseek-v4-pro")
        self.assertIn("Body here.", body)
        provider, model = checks.split_model(data["model"], "openai")
        self.assertEqual(provider, "deepseek")
        self.assertEqual(model, "deepseek-v4-pro")


class ConfigTest(unittest.TestCase):
    def test_opencode_compatible_provider(self):
        config = checks.opencode_config(
            "openai-compatible",
            "deepseek-v4-pro",
            "secret",
            "https://api.oneprovider.dev/v1",
        )
        provider = config["provider"]["openai-compatible"]
        self.assertEqual(provider["npm"], "@ai-sdk/openai-compatible")
        self.assertEqual(provider["options"]["apiKey"], "secret")
        self.assertEqual(
            provider["options"]["baseURL"], "https://api.oneprovider.dev/v1"
        )
        self.assertEqual(config["permission"]["edit"], "deny")
        self.assertEqual(config["permission"]["bash"]["*"], "deny")
        self.assertEqual(config["permission"]["bash"]["git diff*"], "allow")

    def test_opencode_named_provider_omits_npm(self):
        config = checks.opencode_config("anthropic", "claude-sonnet-4-5", "secret", "")
        self.assertNotIn("npm", config["provider"]["anthropic"])
        self.assertNotIn("baseURL", config["provider"]["anthropic"]["options"])

    def test_nextcli_maps_compatible_provider_to_openai(self):
        text = checks.nextcli_config(
            "openai-compatible",
            'model "quoted"',
            "sk-secret",
            "https://api.example.com/v1",
        )
        self.assertIn("provider: openai", text)
        self.assertIn('model: "model \\"quoted\\""', text)
        self.assertIn('apiKey: "sk-secret"', text)
        self.assertIn('apiBase: "https://api.example.com/v1"', text)

    def test_review_payload_single_and_range(self):
        verdict = {
            "verdict": "FAIL",
            "reason": "Two issues",
            "suggestions": [
                {
                    "file": "a.ts",
                    "start_line": 3,
                    "line": 3,
                    "replacement": "const a = 1",
                    "comment": "one line",
                },
                {
                    "file": "b.ts",
                    "start_line": 4,
                    "line": 6,
                    "replacement": "line\nline",
                    "comment": "range",
                },
            ],
        }
        payload = checks.review_payload("security-review", "abc", verdict)
        self.assertEqual(payload["comments"][0]["line"], 3)
        self.assertNotIn("start_line", payload["comments"][0])
        self.assertEqual(payload["comments"][1]["start_line"], 4)
        self.assertEqual(payload["comments"][1]["line"], 6)
        self.assertIn("```suggestion\n", payload["comments"][0]["body"])
        self.assertTrue(payload["body"].startswith("## check/security-review"))

    def test_conclusion(self):
        self.assertEqual(checks.conclusion_for("PASS", "neutral"), "success")
        self.assertEqual(checks.conclusion_for("SKIP", "failure"), "success")
        self.assertEqual(checks.conclusion_for("FAIL", "neutral"), "failure")
        self.assertEqual(checks.conclusion_for("ERROR", "neutral"), "neutral")
        self.assertEqual(checks.conclusion_for("ERROR", "failure"), "failure")


INSTALL_SCRIPT = Path(__file__).with_name("install-agent.sh")

# Stubs log their argv (one call per line) to $STUB_LOG.
NPM_STUB = """#!/usr/bin/env bash
echo "npm $*" >> "$STUB_LOG"
if [ "$1" = install ]; then
  printf '#!/usr/bin/env bash\\necho "%s"\\n' "${3##*@}" > "$STUB_BIN/cn"
  chmod +x "$STUB_BIN/cn"
fi
"""
GIT_STUB = """#!/usr/bin/env bash
echo "git $*" >> "$STUB_LOG"
if [ "$3" = checkout ]; then
  mkdir -p "$2/extensions/cli/dist"
  printf '#!/usr/bin/env bash\\necho 0.0.0-dev\\n' > "$2/extensions/cli/dist/cn.js"
fi
"""


class InstallAgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bin = root / "bin"
        self.bin.mkdir()
        self.log = root / "calls.log"
        self.log.touch()
        self.home = root / "home"
        self.home.mkdir()
        self.src = root / "src"
        for name, body in (("npm", NPM_STUB), ("git", GIT_STUB)):
            stub = self.bin / name
            stub.write_text(body)
            stub.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def install_cn(self, version: str):
        cn = self.bin / "cn"
        cn.write_text(f"#!/usr/bin/env bash\necho {version}\n")
        cn.chmod(0o755)

    def run_install(self, **env: str):
        full_env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.home),
            "STUB_LOG": str(self.log),
            "STUB_BIN": str(self.bin),
            "CHECKS_AGENT": "nextcli",
            "CHECKS_NEXTCLI_SRC_DIR": str(self.src),
            **env,
        }
        result = subprocess.run(
            ["bash", str(INSTALL_SCRIPT)], env=full_env, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result, self.log.read_text().splitlines()

    def test_default_version_is_pinned(self):
        result, calls = self.run_install()
        self.assertEqual(calls, ["npm install -g @nextcli/cli@0.1.1"])
        self.assertEqual(result.stdout.strip(), "0.1.1")

    def test_agent_version_pins_nextcli(self):
        _, calls = self.run_install(CHECKS_AGENT_VERSION="0.2.0")
        self.assertEqual(calls, ["npm install -g @nextcli/cli@0.2.0"])

    def test_matching_installed_cn_is_kept(self):
        self.install_cn("0.1.1")
        result, calls = self.run_install()
        self.assertEqual(calls, [])
        self.assertEqual(result.stdout.strip(), "0.1.1")

    def test_mismatched_installed_cn_is_reinstalled(self):
        self.install_cn("0.1.0")
        result, calls = self.run_install()
        self.assertEqual(calls, ["npm install -g @nextcli/cli@0.1.1"])
        self.assertEqual(result.stdout.strip(), "0.1.1")

    def test_commit_sha_ref_is_fetched(self):
        sha = "0123456789abcdef0123456789abcdef01234567"
        self.install_cn("0.1.1")  # an installed cn does not skip the source build
        _, calls = self.run_install(
            CHECKS_NEXTCLI_REF=sha, CHECKS_NEXTCLI_REPOSITORY="https://example.test/nextcli.git"
        )
        src = str(self.src)
        self.assertEqual(
            calls,
            [
                f"git -C {src} init --quiet",
                f"git -C {src} remote add origin https://example.test/nextcli.git",
                f"git -C {src} fetch --depth 1 origin {sha}",
                f"git -C {src} checkout --quiet FETCH_HEAD",
                "npm run build:local-deps",
                "npm run build",
            ],
        )
        self.assertTrue((self.home / ".local" / "bin" / "cn").is_symlink())


if __name__ == "__main__":
    unittest.main()
