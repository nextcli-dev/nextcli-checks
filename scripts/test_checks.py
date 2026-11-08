#!/usr/bin/env python3
"""Unit tests for scripts/checks.py. Run: python3 scripts/test_checks.py"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
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
        self.assertEqual(parsed["summary"], "Missing tests")
        self.assertTrue(parsed["complete"])

    def test_claude_envelope_with_fences(self):
        inner = (
            "```json\n"
            '{"verdict":"PASS","reason":"Looks good","suggestions":[]}\n'
            "```"
        )
        raw = json.dumps({"result": inner, "type": "result"})
        parsed = checks.parse_verdict(raw)
        self.assertEqual(parsed["verdict"], "PASS")
        self.assertEqual(parsed["findings"], [])

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
        finding = parsed["findings"][0]
        self.assertEqual(finding["file"], "a.ts")
        self.assertEqual(finding["line"], 4)
        self.assertNotIn("start_line", finding)
        self.assertEqual(finding["title"], "Move it")
        self.assertIn("```\nconst key = process.env.KEY\n```", finding["details"])

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
        self.assertEqual(parsed["findings"][0]["start_line"], 2)
        self.assertEqual(parsed["findings"][0]["line"], 3)

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
        self.assertEqual(parsed["findings"], [])

    def test_findings_format(self):
        raw = json.dumps(
            {
                "verdict": "FAIL",
                "summary": "One bug.",
                "complete": False,
                "findings": [
                    {
                        "file": "src/a.ts",
                        "line": "12",
                        "title": "Off by one",
                        "details": "Use `<`.\n\n```ts\nfor (i = 0; i < n; i++)\n```",
                    },
                    {"file": "src/b.ts", "details": "Whole-file issue. More text."},
                    {"line": 3, "title": "No file"},
                ],
            }
        )
        parsed = checks.parse_verdict(raw)
        self.assertFalse(parsed["complete"])
        self.assertEqual(len(parsed["findings"]), 2)
        self.assertEqual(parsed["findings"][0]["line"], 12)
        self.assertIsNone(parsed["findings"][1]["line"])
        self.assertEqual(parsed["findings"][1]["title"], "Whole-file issue.")

    def test_invalid_returns_none(self):
        self.assertIsNone(checks.parse_verdict("not json at all"))

    def test_last_valid_object_wins(self):
        raw = (
            '{"verdict":"FAIL","reason":"first","suggestions":[]}\n'
            'notes\n{"verdict":"PASS","reason":"second","suggestions":[]}'
        )
        parsed = checks.parse_verdict(raw)
        self.assertEqual(parsed["summary"], "second")


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

    def test_conclusion(self):
        self.assertEqual(checks.conclusion_for("PASS", "neutral"), "success")
        self.assertEqual(checks.conclusion_for("SKIP", "failure"), "success")
        self.assertEqual(checks.conclusion_for("FAIL", "neutral"), "failure")
        self.assertEqual(checks.conclusion_for("ERROR", "neutral"), "neutral")
        self.assertEqual(checks.conclusion_for("ERROR", "failure"), "failure")
        self.assertEqual(checks.conclusion_for("INCOMPLETE", "failure"), "neutral")


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
        self.assertEqual(calls, ["npm install -g @nextcli/cli@0.8.1"])
        self.assertEqual(result.stdout.strip(), "0.8.1")

    def test_agent_version_pins_nextcli(self):
        _, calls = self.run_install(CHECKS_AGENT_VERSION="0.2.0")
        self.assertEqual(calls, ["npm install -g @nextcli/cli@0.2.0"])

    def test_matching_installed_cn_is_kept(self):
        self.install_cn("0.8.1")
        result, calls = self.run_install()
        self.assertEqual(calls, [])
        self.assertEqual(result.stdout.strip(), "0.8.1")

    def test_mismatched_installed_cn_is_reinstalled(self):
        self.install_cn("0.1.0")
        result, calls = self.run_install()
        self.assertEqual(calls, ["npm install -g @nextcli/cli@0.8.1"])
        self.assertEqual(result.stdout.strip(), "0.8.1")

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



class CommentAndLimitTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("PROVIDER_API_KEY", None)

    def test_render_comment_links_partial_note_redaction_and_mentions(self):
        os.environ["PROVIDER_API_KEY"] = "sk-supersecretvalue"
        body = checks.render_comment(
            [
                (
                    "review",
                    {
                        "verdict": "FAIL",
                        "summary": "Found sk-supersecretvalue and ping @alice",
                        "complete": False,
                        "limit": "cost",
                        "usage": {"cost": 0.4, "priced": "exact"},
                        "findings": [
                            {
                                "file": "src/a.ts",
                                "line": 12,
                                "title": "Off by one",
                                "details": "See @bob and `keep@code`.",
                            }
                        ],
                    },
                )
            ],
            "nextcli-dev/nextcli",
            "abcdef1234567890",
            "1",
        )
        self.assertIn("<!-- nextcli-checks -->", body)
        self.assertIn(
            "[`src/a.ts:12`](https://github.com/nextcli-dev/nextcli/blob/abcdef1234567890/src/a.ts#L12)",
            body,
        )
        self.assertIn("**Partial review.**", body)
        self.assertIn("cost limit", body)
        self.assertIn("[REDACTED]", body)
        self.assertNotIn("sk-supersecretvalue", body)
        self.assertNotIn("@alice", body)
        self.assertNotIn("@bob", body)
        self.assertIn("`keep@code`", body)
        self.assertIn("cost $0.40", body)
        self.assertIn("limit $1.00 per check", body)

    def test_render_comment_truncates(self):
        body = checks.render_comment(
            [
                (
                    "review",
                    {
                        "verdict": "FAIL",
                        "summary": "big",
                        "findings": [
                            {"file": "a.ts", "title": "t", "details": "x" * 70_000}
                        ],
                    },
                )
            ],
            "o/r",
            "abc1234",
            "",
        )
        self.assertLessEqual(len(body), checks.GITHUB_TEXT_LIMIT)
        self.assertIn("cut to fit in a comment", body)

    def test_incomplete_gets_the_partial_note(self):
        verdict = {
            "verdict": "INCOMPLETE",
            "summary": "The agent stopped at its cost limit ($1) before it returned a result.",
            "complete": False,
            "limit": "cost",
            "findings": [],
        }
        note = checks.partial_note(verdict)
        self.assertIn("**Partial review.**", note)
        self.assertIn("cost limit", note)

    def test_limit_exit_keeps_a_verdict_and_marks_it_partial(self):
        summary = checks.read_run_summary(
            "noise\n"
            '{"status":"limit","limit":"cost","usage":{"cost":0.9,"priced":"exact",'
            '"promptTokens":10,"completionTokens":20}}\n'
        )
        annotated = checks.annotate_verdict(
            {
                "verdict": "FAIL",
                "summary": "bug",
                "complete": True,
                "findings": [],
            },
            summary,
            True,
        )
        self.assertEqual(annotated["verdict"], "FAIL")
        self.assertFalse(annotated["complete"])
        self.assertEqual(annotated["limit"], "cost")
        self.assertEqual(annotated["usage"]["cost"], 0.9)
        self.assertIn("Partial review", checks.partial_note(annotated))

    def test_limit_exit_without_a_verdict_stays_incomplete(self):
        # run-check.sh writes this when exit code 3 has no parseable verdict.
        verdict = {
            "verdict": "INCOMPLETE",
            "summary": "The agent stopped at its cost limit ($1) before it returned a result.",
            "complete": False,
            "findings": [],
        }
        annotated = checks.annotate_verdict(verdict, None, True)
        self.assertEqual(annotated["verdict"], "INCOMPLETE")
        self.assertEqual(annotated["limit"], "cost")
        self.assertEqual(checks.conclusion_for(annotated["verdict"], "failure"), "neutral")
        self.assertIn("Partial review", checks.partial_note(annotated))


class RedactTest(unittest.TestCase):
    def test_redacts_every_occurrence(self):
        text = 'reason: key sk-abcdef123456 leaked; again sk-abcdef123456'
        self.assertEqual(
            checks.redact_text(text, ["sk-abcdef123456"]),
            "reason: key [REDACTED] leaked; again [REDACTED]",
        )

    def test_ignores_empty_and_short_values(self):
        self.assertEqual(checks.redact_text("abc 1234", ["", "1234"]), "abc 1234")

    def test_longest_secret_first(self):
        self.assertEqual(
            checks.redact_text("tok=ghs_longtoken123", ["ghs_long", "ghs_longtoken123"]),
            "tok=[REDACTED]",
        )

    def test_redact_command_rewrites_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            verdict = Path(tmp) / "verdict.json"
            verdict.write_text(json.dumps({"verdict": "FAIL", "reason": "sk-secretvalue99"}))
            env = {**__import__("os").environ, "PROVIDER_API_KEY": "sk-secretvalue99"}
            subprocess.run(
                ["python3", "-I", str(MODULE_PATH), "redact", str(verdict), str(Path(tmp) / "missing")],
                check=True,
                env=env,
            )
            self.assertEqual(json.loads(verdict.read_text())["reason"], "[REDACTED]")


class AgentExitCodeTest(unittest.TestCase):
    """run-check.sh must turn a non-zero agent exit into a verdict, not die on it."""

    NAME = "exitcode-test"
    VALID = json.dumps(
        {"verdict": "FAIL", "summary": "found one", "complete": False, "findings": []}
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.scripts = self.root / "scripts"
        shutil.copytree(Path(__file__).parent, self.scripts, ignore=shutil.ignore_patterns("__pycache__"))
        self.check = self.root / f"{self.NAME}.md"
        self.check.write_text("---\nname: Exit code test\ndescription: x\n---\nCheck something.\n")
        Path("/tmp/pr-truncated.diff").write_text("diff --git a/a b/a\n")
        self.verdict = Path(f"/tmp/check-{self.NAME}.verdict.json")
        # GNU timeout isn't on macOS. The tests don't wait, so a shim is enough.
        self.bin = self.root / "bin"
        self.bin.mkdir()
        shim = self.bin / "timeout"
        shim.write_text('#!/usr/bin/env bash\nshift\nexec "$@"\n')
        shim.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()
        for path in Path("/tmp").glob(f"check-{self.NAME}.*"):
            path.unlink()

    def run_check(self, exit_code: int, stdout: str = ""):
        agent = self.scripts / "agents" / "nextcli.sh"
        agent.write_text(f"#!/usr/bin/env bash\nprintf '%s' '{stdout}'\necho 'agent stderr' >&2\nexit {exit_code}\n")
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.root),
            "CHECKS_AGENT": "nextcli",
            "CHECKS_PROVIDER": "openai-compatible",
            "CHECKS_MODEL": "m",
            "CHECKS_MAX_COST": "1",
        }
        result = subprocess.run(
            ["bash", str(self.scripts / "run-check.sh"), str(self.check)],
            env=env, capture_output=True, text=True,
        )
        return result, json.loads(self.verdict.read_text())

    def test_timeout_becomes_an_error_verdict(self):
        result, verdict = self.run_check(124)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(verdict["verdict"], "ERROR")
        self.assertIn("timed out", verdict["summary"])

    def test_cost_limit_without_an_answer_is_incomplete(self):
        result, verdict = self.run_check(3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(verdict["verdict"], "INCOMPLETE")

    def test_cost_limit_keeps_the_answer_the_agent_gave(self):
        result, verdict = self.run_check(3, self.VALID)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(verdict["verdict"], "FAIL")
        self.assertFalse(verdict["complete"])

    def test_rejected_configuration_becomes_an_error_verdict(self):
        result, verdict = self.run_check(2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(verdict["verdict"], "ERROR")


if __name__ == "__main__":
    unittest.main()
