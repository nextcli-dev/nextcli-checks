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

    def test_a_verdict_with_no_findings_did_not_review_a_diff(self):
        verdict = {"verdict": "FAIL", "complete": False, "findings": []}
        self.assertTrue(checks.reviewed_nothing(verdict, "diff --git a/a b/a\n"))
        self.assertFalse(checks.reviewed_nothing(verdict, "   "))
        with_findings = {
            "verdict": "FAIL",
            "complete": False,
            "findings": [{"file": "a.ts", "title": "Bug", "details": "x"}],
        }
        self.assertFalse(
            checks.reviewed_nothing(with_findings, "diff --git a/a b/a\n")
        )
        finished = {"verdict": "FAIL", "complete": True, "findings": []}
        self.assertFalse(checks.reviewed_nothing(finished, "diff --git a/a b/a\n"))

    def test_prompt_says_a_missing_path_is_not_an_empty_diff(self):
        text = checks.render_prompt("Look for bugs.", "diff --git a/a b/a\n")
        self.assertIn("does not depend on a tool call succeeding", text)
        self.assertIn("diff --git a/a b/a", text)
        retry = checks.render_prompt(
            "Look for bugs.", "diff", empty_review=True
        )
        self.assertIn("did not review the diff", retry)

    def test_invalid_returns_none(self):
        self.assertIsNone(checks.parse_verdict("not json at all"))

    def test_verdict_case_does_not_matter(self):
        # Run 42669709525: a review at its cost cap answered "pass" and was
        # thrown away as having no verdict.
        for written, expected in (("pass", "PASS"), (" Fail ", "FAIL")):
            raw = json.dumps(
                {"verdict": written, "summary": "ok", "complete": False, "findings": []}
            )
            parsed = checks.parse_verdict(raw)
            self.assertEqual(parsed["verdict"], expected)
            self.assertFalse(parsed["complete"])

    def test_a_non_string_verdict_is_still_rejected(self):
        self.assertIsNone(checks.parse_verdict('{"verdict": 1, "findings": []}'))
        self.assertIsNone(checks.parse_verdict('{"verdict": "maybe", "findings": []}'))

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

    def test_nextcli_omits_output_cap_by_default(self):
        text = checks.nextcli_config("deepseek", "deepseek-v4.1-flash", "k", "")
        self.assertNotIn("maxTokens", text)

    def test_nextcli_writes_output_cap(self):
        text = checks.nextcli_config(
            "deepseek", "deepseek-v4.1-flash", "k", "", max_output_tokens=32000
        )
        self.assertIn("    defaultCompletionOptions:\n      maxTokens: 32000\n", text)

    def test_nextcli_config_reads_output_cap_from_env(self):
        env = {
            **os.environ,
            "CHECKS_PROVIDER": "deepseek",
            "CHECKS_MODEL": "deepseek-v4.1-flash",
            "PROVIDER_API_KEY": "k",
            "CHECKS_MAX_OUTPUT_TOKENS": "32000",
        }
        result = subprocess.run(
            ["python3", "-I", str(MODULE_PATH), "nextcli-config"],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("maxTokens: 32000", result.stdout)

        env["CHECKS_MAX_OUTPUT_TOKENS"] = "lots"
        result = subprocess.run(
            ["python3", "-I", str(MODULE_PATH), "nextcli-config"],
            env=env, capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("max-output-tokens", result.stderr)

    def test_conclusion(self):
        self.assertEqual(checks.conclusion_for("PASS", "neutral"), "success")
        self.assertEqual(checks.conclusion_for("PASS", "neutral", complete=False), "neutral")
        self.assertEqual(checks.conclusion_for("FAIL", "neutral", complete=False), "failure")
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

    def _no_verdict(self):
        return {
            "verdict": "ERROR",
            "summary": "The agent did not return a valid verdict.",
            "complete": True,
            "findings": [],
        }

    def test_an_agent_error_is_named_and_its_usage_kept(self):
        summary = checks.read_run_summary(
            "noise\n"
            '{"status":"error","message":"429 Too Many Requests",'
            '"usage":{"cost":0.4,"priced":"exact","promptTokens":7,"completionTokens":8}}\n'
        )
        annotated = checks.annotate_verdict(self._no_verdict(), summary, False)
        self.assertEqual(annotated["verdict"], "ERROR")
        self.assertIn("did not return a valid verdict", annotated["summary"])
        self.assertIn("stopped with an error: 429 Too Many Requests", annotated["summary"])
        self.assertEqual(annotated["usage"]["promptTokens"], 7)
        self.assertNotIn("limit", annotated)

    def test_a_finished_run_without_an_answer_says_so(self):
        summary = checks.read_run_summary(
            '{"status":"done","limit":null,"usage":{"cost":0.8,"priced":"exact",'
            '"promptTokens":5,"completionTokens":6}}\n'
        )
        annotated = checks.annotate_verdict(self._no_verdict(), summary, False)
        self.assertIn("finished without writing an answer", annotated["summary"])
        self.assertEqual(annotated["usage"]["cost"], 0.8)

    def test_a_long_error_message_is_cut(self):
        summary = {"status": "error", "message": "x" * 5000}
        annotated = checks.annotate_verdict(self._no_verdict(), summary, False)
        self.assertLess(len(annotated["summary"]), 700)

    def test_a_verdict_keeps_its_summary_when_the_run_is_done(self):
        summary = {"status": "done", "limit": None, "usage": {"cost": 0.1}}
        verdict = {"verdict": "PASS", "summary": "fine", "complete": True, "findings": []}
        annotated = checks.annotate_verdict(verdict, summary, False)
        self.assertEqual(annotated["summary"], "fine")


def _event(ts, **attrs):
    return {"ts": ts, "dur": attrs.pop("dur", 1000), "name": "llm.request", "attrs": attrs}


def _header(ts, run_id):
    return {"ts": ts, "name": "header", "attrs": {"version": "x", "runId": run_id, "sessionId": "s"}}


def _write_trace(directory, events):
    path = Path(directory) / "check.calls.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return path


class SessionReportTest(unittest.TestCase):
    """The call table reads NextCLI's NEXTCLI_TRACE=basic trace."""

    # A run that read a file, had one attempt discarded, then ran out of output
    # tokens while thinking.
    CUT_OFF_RUN = [
        _header(100, "r1"),
        _event(101, dur=4000, model="m", usage={
            "prompt_tokens": 25000, "completion_tokens": 900,
            "prompt_tokens_details": {"cached_tokens": 1000}},
            costUsd=0.01, priced="exact", finishReason="tool_calls", ttftMs=800,
            contentChars=0, reasoningChars=1200, toolCalls=["Read", "Read"]),
        _event(102, dur=9000, model="m", usage={"prompt_tokens": 31000, "completion_tokens": 300},
               costUsd=0.01, contentChars=120, reasoningChars=0, toolCalls=[],
               discarded=True, error="socket hang up"),
        _event(103, dur=41000, model="m", usage={"prompt_tokens": 31000, "completion_tokens": 32000},
               costUsd=0.05, finishReason="length", ttftMs=900, contentChars=0,
               reasoningChars=90000, toolCalls=[]),
    ]

    def _read(self, path, size):
        return {
            "toolCall": {"function": {"name": "Read", "arguments": json.dumps({"filepath": path})}},
            "output": [{"content": "x" * size}],
        }

    def _runs(self, events):
        with tempfile.TemporaryDirectory() as tmp:
            return checks.read_trace(_write_trace(tmp, events))

    def test_reads_every_field_cn_writes(self):
        [run] = self._runs(self.CUT_OFF_RUN)
        self.assertEqual(run["run_id"], "r1")
        first, discarded, last = run["calls"]
        self.assertEqual(first["cached_tokens"], 1000)
        self.assertEqual(first["ttft_ms"], 800)
        self.assertEqual(first["priced"], "exact")
        self.assertEqual(first["tool_calls"], ["Read", "Read"])
        self.assertTrue(discarded["discarded"])
        self.assertEqual(discarded["error"], "socket hang up")
        self.assertEqual(last["finish_reason"], "length")

    def test_lists_each_call_and_says_the_run_was_cut_off(self):
        report = checks.session_report(self._runs(self.CUT_OFF_RUN))
        self.assertIn("2 model calls (1 discarded)", report)
        self.assertIn("87,000 prompt", report)
        self.assertIn("$0.07", report)
        self.assertIn("discarded, error: socket hang up", report)
        self.assertIn("cut off at the output cap (32,000 completion tokens)", report)
        self.assertIn("Tool calls: Read x2", report)
        self.assertNotIn("Run 1", report)

    def test_splits_a_check_and_its_retry_in_time_order(self):
        retry = [_header(200, "r2"), _event(201, model="m", usage={"prompt_tokens": 5, "completion_tokens": 5},
                                            costUsd=0.02, finishReason="stop", contentChars=40)]
        # run-check.sh concatenates the files in name order, which is not time order
        runs = self._runs(retry + self.CUT_OFF_RUN)
        self.assertEqual([run["run_id"] for run in runs], ["r1", "r2"])
        report = checks.session_report(runs)
        self.assertIn("Run 1: 2 model calls (1 discarded)", report)
        self.assertIn("Run 2 (retry): 1 model calls (0 discarded)", report)
        self.assertIn("3 model calls (1 discarded)", report.splitlines()[0])

    def test_tells_an_empty_reply_after_thinking_from_a_truly_empty_one(self):
        thinking = _event(2, model="m", usage={"prompt_tokens": 9, "completion_tokens": 820,
                                               "completion_tokens_details": {"reasoning_tokens": 800}},
                          finishReason="stop", contentChars=0, reasoningChars=0, toolCalls=[])
        empty = _event(3, model="m", usage={"prompt_tokens": 9, "completion_tokens": 0},
                       finishReason="stop", contentChars=0, reasoningChars=0, toolCalls=[])
        [run] = self._runs([_header(1, "r"), thinking])
        self.assertEqual(run["calls"][0]["reasoning_tokens"], 800)
        self.assertEqual(
            checks.run_ending(run["calls"]),
            "The last call returned no answer and no tool calls after thinking (800 reasoning tokens).",
        )
        self.assertIn("0 (800 tok)", checks.session_report([run]))
        [run] = self._runs([_header(1, "r"), empty])
        self.assertEqual(
            checks.run_ending(run["calls"]),
            "The last call returned nothing: no answer, no tool calls and no reasoning.",
        )

    def test_reports_a_failed_or_stopped_call(self):
        failed = _event(2, model="m", error="Bad request", status=400, aborted=False)
        [run] = self._runs([_header(1, "r"), failed])
        self.assertEqual(checks.run_ending(run["calls"]), "The last call failed with a provider error 400.")
        self.assertIn("error 400: Bad request", checks.session_report([run]))
        stopped = _event(2, model="m", usage={"prompt_tokens": 1, "completion_tokens": 1},
                         contentChars=3, aborted=True)
        [run] = self._runs([_header(1, "r"), stopped])
        self.assertEqual(checks.run_ending(run["calls"]), "The last call was stopped.")

    def test_says_nothing_about_a_run_that_answered(self):
        answered = _event(2, model="m", finishReason="stop", contentChars=40, toolCalls=[])
        [run] = self._runs([_header(1, "r"), answered])
        self.assertIsNone(checks.run_ending(run["calls"]))

    def test_reports_tool_result_sizes_and_repeated_reads(self):
        session = {"history": [{"toolCallStates": [
            self._read("src/a.ts", 5000),
            self._read("src/b.ts", 200),
            self._read("src/a.ts", 5000),
        ]}]}
        report = checks.session_report(self._runs(self.CUT_OFF_RUN), session)
        self.assertIn("Characters returned: Read 10,200", report)
        self.assertIn("Largest results: Read src/a.ts 5,000", report)
        self.assertIn("Repeated: Read src/a.ts x2", report)

    def test_command_skips_bad_lines_and_a_header_only_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "check.calls.jsonl"
            path.write_text(
                json.dumps(self.CUT_OFF_RUN[0]) + "\nnot json\n[1]\n\n"
                + json.dumps({"ts": 5, "name": "other"}) + "\n"
                + json.dumps(self.CUT_OFF_RUN[3]) + "\n"
            )
            result = subprocess.run(
                ["python3", "-I", str(MODULE_PATH), "session-report", "--calls", str(path)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("1 model calls (0 discarded)", result.stdout)
            path.write_text(json.dumps(self.CUT_OFF_RUN[0]) + "\n")
            result = subprocess.run(
                ["python3", "-I", str(MODULE_PATH), "session-report", "--calls", str(path)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("0 model calls (0 discarded)", result.stdout)


class RetryBudgetTest(unittest.TestCase):
    def test_leaves_what_the_first_run_did_not_spend(self):
        summary = {"status": "error", "usage": {"cost": 0.01955}}
        remaining, spent = checks.retry_budget(summary, 2.0)
        self.assertAlmostEqual(remaining, 1.98045)
        self.assertAlmostEqual(spent, 0.01955)

    def test_no_retry_without_a_known_cost(self):
        self.assertIsNone(checks.retry_budget(None, 2.0))
        self.assertIsNone(checks.retry_budget({"status": "error"}, 2.0))
        self.assertIsNone(checks.retry_budget({"status": "error", "usage": {"cost": "x"}}, 2.0))

    def test_no_retry_when_little_is_left(self):
        self.assertIsNone(checks.retry_budget({"status": "error", "usage": {"cost": 1.9}}, 2.0))

    def test_command_rounds_the_budget_down(self):
        with tempfile.TemporaryDirectory() as tmp:
            err = Path(tmp) / "check.err"
            err.write_text('noise\n{"status":"error","message":"x","usage":{"cost":0.00005}}\n')
            result = subprocess.run(
                ["python3", "-I", str(MODULE_PATH), "retry-budget", "--stderr", str(err), "--max-cost", "1"],
                capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["0.9999", "0.000050"])

    def test_annotate_adds_the_earlier_run_to_the_cost(self):
        annotated = checks.annotate_verdict(
            {"verdict": "PASS", "summary": "ok", "complete": True, "findings": []},
            {"status": "done", "usage": {"cost": 0.5, "priced": "exact"}},
            False,
            0.25,
        )
        self.assertEqual(annotated["usage"]["cost"], 0.75)


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
            if path.is_dir():
                shutil.rmtree(path)
            else:
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
        answer = json.dumps(
            {
                "verdict": "FAIL",
                "summary": "found one",
                "complete": False,
                "findings": [{"file": "a.ts", "title": "Bug", "details": "x"}],
            }
        )
        result, verdict = self.run_check(3, answer)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(verdict["verdict"], "FAIL")
        self.assertFalse(verdict["complete"])
        self.assertEqual(len(verdict["findings"]), 1)
        self.assertEqual(checks.conclusion_for(verdict["verdict"], "neutral", verdict["complete"]), "failure")

    def test_unfinished_verdict_with_no_findings_is_not_a_review(self):
        # A run that finished on its own (exit 0) and looked at nothing.
        result, verdict = self.run_check(0, self.VALID)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(verdict["verdict"], "INCOMPLETE")
        self.assertIn("did not review the diff", verdict["summary"])

    def run_sequence(self, runs, timeout="10", max_cost="2"):
        """An agent that behaves like runs[i] on its i-th run: (exit code,
        stdout, last stderr line). Returns the result, the verdict, and the
        --max-cost each run got."""
        record = self.root / "runs"
        agent = self.scripts / "agents" / "nextcli.sh"
        lines = ["#!/usr/bin/env bash", f"echo \"$CHECKS_MAX_COST\" >> '{record}'", f"n=$(wc -l < '{record}' | tr -d ' ')"]
        for index, (code, stdout, stderr) in enumerate(runs, start=1):
            lines += [
                f"if [ \"$n\" -eq {index} ]; then",
                f"  printf '%s' '{stdout}'",
                f"  echo '{stderr}' >&2",
                f"  exit {code}",
                "fi",
            ]
        lines.append("exit 1")
        agent.write_text("\n".join(lines) + "\n")
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.root),
            "CHECKS_AGENT": "nextcli",
            "CHECKS_PROVIDER": "openai-compatible",
            "CHECKS_MODEL": "m",
            "CHECKS_MAX_COST": max_cost,
            "CHECKS_TIMEOUT": timeout,
        }
        result = subprocess.run(
            ["bash", str(self.scripts / "run-check.sh"), str(self.check)],
            env=env, capture_output=True, text=True,
        )
        return result, json.loads(self.verdict.read_text()), record.read_text().split()

    def test_limit_exit_with_no_findings_is_kept_as_a_partial_review(self):
        # Run 42693558895: a review that stopped at its cost cap after 82
        # calls answered PASS with no findings. It was retried as "reviewed
        # nothing", and the retry ran out of time.
        answer = json.dumps(
            {"verdict": "pass", "summary": "checked most files", "complete": False, "findings": []}
        )
        summary = '{"status":"limit","limit":"cost","usage":{"cost":1.84}}'
        result, verdict, limits = self.run_sequence([(3, answer, summary)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(limits, ["2"])
        self.assertEqual(verdict["verdict"], "PASS")
        self.assertFalse(verdict["complete"])
        self.assertEqual(verdict["limit"], "cost")
        self.assertEqual(checks.conclusion_for(verdict["verdict"], "neutral", verdict["complete"]), "neutral")

    def test_empty_review_is_retried_with_the_budget_left(self):
        empty = json.dumps({"verdict": "PASS", "summary": "x", "complete": False, "findings": []})
        found = json.dumps(
            {"verdict": "FAIL", "summary": "found one", "complete": True,
             "findings": [{"file": "a.ts", "title": "Bug", "details": "x"}]}
        )
        result, verdict, limits = self.run_sequence([
            (0, empty, '{"status":"done","limit":null,"usage":{"cost":0.5}}'),
            (0, found, '{"status":"done","limit":null,"usage":{"cost":0.3}}'),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(limits, ["2", "1.5000"])
        self.assertEqual(verdict["verdict"], "FAIL")
        self.assertAlmostEqual(verdict["usage"]["cost"], 0.8)

    def test_empty_review_retry_that_times_out_is_incomplete_not_an_error(self):
        empty = json.dumps({"verdict": "PASS", "summary": "x", "complete": False, "findings": []})
        result, verdict, limits = self.run_sequence([
            (0, empty, '{"status":"done","limit":null,"usage":{"cost":0.5}}'),
            (124, "", ""),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(limits), 2)
        self.assertEqual(verdict["verdict"], "INCOMPLETE")
        self.assertIn("ran out of time", verdict["summary"])

    def test_empty_review_is_not_retried_without_budget(self):
        empty = json.dumps({"verdict": "PASS", "summary": "x", "complete": False, "findings": []})
        result, verdict, limits = self.run_sequence([
            (0, empty, '{"status":"done","limit":null,"usage":{"cost":1.95}}'),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(limits, ["2"])
        self.assertEqual(verdict["verdict"], "INCOMPLETE")
        self.assertIn("did not review the diff", verdict["summary"])

    def test_no_retry_with_less_than_a_third_of_the_time_left(self):
        # A 1-minute limit is under the 2-minute floor for a retry.
        result, verdict, limits = self.run_sequence(
            [(1, "", '{"status":"error","message":"boom","usage":{"cost":0.1}}')], timeout="1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(limits, ["2"])
        self.assertEqual(verdict["verdict"], "ERROR")
        self.assertIn("too little", result.stdout)

    def test_turn_limit_without_an_answer_names_the_turn_limit(self):
        result, verdict, _limits = self.run_sequence(
            [(3, "", '{"status":"limit","limit":"turns","usage":{"cost":0.4}}')]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(verdict["verdict"], "INCOMPLETE")
        self.assertIn("turn limit", verdict["summary"])

    def test_rejected_configuration_becomes_an_error_verdict(self):
        result, verdict = self.run_check(2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(verdict["verdict"], "ERROR")

    def run_twice(self, first_cost: float, timeout: str = "10"):
        """An agent whose first run errors at `first_cost` and whose second run
        answers. Returns the result, the verdict and the --max-cost of each run."""
        answer = json.dumps(
            {"verdict": "FAIL", "summary": "found one", "complete": True,
             "findings": [{"file": "a.ts", "title": "Bug", "details": "x"}]}
        )
        runs = self.root / "runs"
        agent = self.scripts / "agents" / "nextcli.sh"
        agent.write_text(
            "#!/usr/bin/env bash\n"
            f"echo \"$CHECKS_MAX_COST\" >> '{runs}'\n"
            f"if [ \"$(wc -l < '{runs}')\" -eq 1 ]; then\n"
            f"  echo '{{\"status\":\"error\",\"message\":\"The model finished without writing an answer.\",\"usage\":{{\"cost\":{first_cost}}}}}' >&2\n"
            "  exit 1\n"
            "fi\n"
            f"printf '%s' '{answer}'\n"
            "echo '{\"status\":\"done\",\"limit\":null,\"usage\":{\"cost\":0.3}}' >&2\n"
        )
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.root),
            "CHECKS_AGENT": "nextcli",
            "CHECKS_PROVIDER": "openai-compatible",
            "CHECKS_MODEL": "m",
            "CHECKS_MAX_COST": "2",
            "CHECKS_TIMEOUT": timeout,
        }
        result = subprocess.run(
            ["bash", str(self.scripts / "run-check.sh"), str(self.check)],
            env=env, capture_output=True, text=True,
        )
        return result, json.loads(self.verdict.read_text()), runs.read_text().split()

    def test_error_is_not_retried_without_time_left(self):
        # A zero-minute limit leaves nothing for a retry after the first run
        result, verdict, limits = self.run_twice(0.5, timeout="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(limits, ["2"])
        self.assertEqual(verdict["verdict"], "ERROR")
        self.assertIn("too little", result.stdout)

    def test_error_is_retried_with_the_budget_left(self):
        result, verdict, limits = self.run_twice(0.5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(limits, ["2", "1.5000"])
        self.assertEqual(verdict["verdict"], "FAIL")
        self.assertAlmostEqual(verdict["usage"]["cost"], 0.8)

    def test_error_is_not_retried_when_little_budget_is_left(self):
        result, verdict, limits = self.run_twice(1.9)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(limits, ["2"])
        self.assertEqual(verdict["verdict"], "ERROR")
        self.assertIn("finished without writing an answer", verdict["summary"])


class DepsPlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel: str, text: str = "") -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def plan(self, *changed: str) -> list[tuple[str, str]]:
        return [(s["ecosystem"], s["dir"]) for s in checks.deps_plan(self.root, list(changed))]

    def rows(self, steps: list[dict]) -> list[tuple[str, str, str, str]]:
        rows = []
        for step in steps:
            if step["action"] == "run":
                rows.append((step["ecosystem"], step["deps"], "ok", ""))
            else:
                rows.append((step["ecosystem"], step["deps"], step["status"], step["reason"]))
        return rows

    def test_nearest_lockfile_in_a_repository_without_workspaces(self):
        self.write("package-lock.json", "{}")
        self.write("packages/a/package-lock.json", "{}")
        self.write("packages/a/src/x.ts")
        self.assertEqual(self.plan("packages/a/src/x.ts", "tools/y.js"), [("npm", "packages/a"), ("npm", "")])

    def test_files_that_are_not_code_install_nothing(self):
        self.write("package-lock.json", "{}")
        self.write("pyproject.toml", "[project]\nname='x'\n")
        self.assertEqual(self.plan("README.md", "docs/guide.md", ".github/workflows/ci.yml"), [])

    def test_only_the_ecosystem_of_the_changed_file(self):
        # A Python helper in a TypeScript repository installs Python only.
        self.write("package-lock.json", "{}")
        self.write("requirements.txt", "requests==2.32.3\n")
        self.assertEqual(self.plan("scripts/tool.py"), [("pip", "")])
        self.assertEqual(self.plan("src/a.ts"), [("npm", "")])

    def test_dotnet_is_not_installed(self):
        self.write("App.sln")
        self.write("App.csproj")
        self.write("global.json", '{"sdk": {"version": "8.0.403"}}')
        steps = checks.deps_plan(self.root, ["Program.cs"])
        self.assertFalse(any(step["action"] == "run" for step in steps))
        self.assertEqual([(step["ecosystem"], step["status"]) for step in steps], [("nuget", "unsupported")])
        text = checks.render_dependencies(self.rows(steps))
        self.assertIn("NuGet is not installed", text)
        self.assertIn("runs project code", text)
        self.assertNotIn("dotnet", text)
        self.assertNotIn("App.sln", text)

    def test_java_does_not_schedule_maven_or_gradle(self):
        self.write("pom.xml")
        self.write("app/build.gradle")
        steps = checks.deps_plan(self.root, ["app/src/A.java"])
        self.assertFalse(any(step["action"] == "run" for step in steps))
        self.assertEqual(
            [(step["ecosystem"], step["status"]) for step in steps],
            [("maven", "unsupported"), ("gradle", "unsupported")],
        )
        text = checks.render_dependencies(self.rows(steps))
        self.assertIn("Maven and Gradle are not installed", text)
        self.assertNotIn("mvn", text)
        self.assertNotIn("gradlew", text)

    def test_other_ecosystems(self):
        self.write("go.mod", "module x\n\ngo 1.23.4\n")
        self.write("Cargo.toml")
        self.write("Cargo.lock")
        self.write("pom.xml")
        self.write("mod/pom.xml")
        plan = {s["ecosystem"]: s for s in checks.deps_plan(self.root, ["a.go", "src/lib.rs", "mod/src/A.java"])}
        self.assertEqual(plan["go"]["image"], "golang:1.23")
        self.assertEqual(plan["go"]["argv"], ["go", "mod", "download"])
        self.assertEqual(plan["cargo"]["argv"], ["cargo", "fetch", "--locked"])
        self.assertEqual(plan["maven"]["status"], "unsupported")
        self.assertEqual(plan["gradle"]["status"], "unsupported")

    def test_pip_installs_wheels_from_a_requirements_file(self):
        self.write("a/requirements.txt", "requests==2.32.3\n")
        self.write("b/requirements-dev.txt", "pytest==8.3.0\n")
        steps = {step["dir"]: step for step in checks.deps_plan(self.root, ["a/x.py", "b/y.py"])}
        self.assertEqual(
            steps["a"]["argv"],
            ["pip", "install", "--target", "/work/.checks-deps/python/a", "--only-binary", ":all:", "-r", "requirements.txt"],
        )
        self.assertEqual(steps["a"]["deps"], ".checks-deps/python/a")
        self.assertEqual(steps["b"]["argv"][-1], "requirements-dev.txt")
        self.assertEqual(steps["b"]["deps"], ".checks-deps/python/b")
        self.assertNotIn(".", steps["a"]["argv"])

    def test_pyproject_without_requirements_is_skipped(self):
        self.write("pyproject.toml", "[project]\nname='x'\n")
        steps = checks.deps_plan(self.root, ["a.py"])
        self.assertEqual([(step["action"], step["reason"]) for step in steps], [("skip", "no-requirements")])
        self.assertNotIn("pip install", json.dumps(steps))
        text = checks.render_dependencies(self.rows(steps))
        self.assertIn("no requirements file", text)
        self.assertIn("would run project code", text)
        self.assertNotIn("`pyproject.toml`", text)

    def test_cargo_without_a_lockfile_is_skipped(self):
        self.write("Cargo.toml", "[package]\nname='x'\nversion='0.1.0'\n")
        steps = checks.deps_plan(self.root, ["src/lib.rs"])
        self.assertEqual([(step["action"], step["reason"]) for step in steps], [("skip", "no-lock")])
        self.assertNotIn("cargo fetch", json.dumps(steps))

    def test_unsafe_project_paths_are_not_planned_or_shown(self):
        self.write("package-lock.json", "{}")
        steps = checks.deps_plan(self.root, ["bad'name/a.ts", "bad\nname/b.ts"])
        blob = json.dumps(steps)
        self.assertNotIn("bad'name", blob)
        self.assertNotIn("bad\nname", blob)
        self.assertTrue(steps)
        self.assertTrue(all(step["reason"] == "unsafe-path" for step in steps))
        text = checks.render_dependencies(self.rows(steps))
        self.assertNotIn("bad", text)
        self.assertIn("cannot be installed safely", text)

    def test_node_version_from_nvmrc_and_engines(self):
        self.write("a/package-lock.json", "{}")
        self.write("a/.nvmrc", "v20.11.0\n")
        self.write("b/package-lock.json", "{}")
        self.write("b/package.json", '{"engines": {"node": ">=24.1"}}')
        images = {s["dir"]: s["image"] for s in checks.deps_plan(self.root, ["a/x.js", "b/y.ts"])}
        self.assertEqual(images, {"a": "node:20", "b": "node:24"})

    def test_pnpm_and_yarn_lockfiles(self):
        self.write("p/pnpm-lock.yaml")
        self.write("y/yarn.lock", "# yarn lockfile v1\n")
        self.write("z/yarn.lock", "__metadata:\n  version: 8\n")
        commands = {s["dir"]: s for s in checks.deps_plan(self.root, ["p/a.ts", "y/a.ts", "z/a.ts"])}
        self.assertEqual(commands["p"]["prelude"], "corepack")
        self.assertIn("--ignore-scripts", commands["p"]["argv"])
        self.assertEqual(commands["y"]["prelude"], "corepack-yarn1")
        self.assertIn("--ignore-scripts", commands["y"]["argv"])
        self.assertNotIn("corepack prepare", commands["y"]["argv"])
        self.assertEqual(commands["z"]["prelude"], "corepack")
        self.assertIn("--mode=skip-build", commands["z"]["argv"])

    def test_dependencies_and_the_base_checkout_are_not_projects(self):
        self.write("package-lock.json", "{}")
        self.assertEqual(self.plan("node_modules/x/index.js", ".nextcli-checks-base/a.ts", ".checks-deps/go/x.go"), [])

    def test_too_many_projects_are_capped_in_changed_file_order(self):
        indexes = list(range(checks.MAX_PROJECTS_PER_ECOSYSTEM + 2))
        changed = []
        for i in reversed(indexes):
            self.write(f"p{i}/package-lock.json", "{}")
            changed.append(f"p{i}/a.ts")
        steps = checks.deps_plan(self.root, changed)
        runs = [step["dir"] for step in steps if step["action"] == "run"]
        capped = [step["dir"] for step in steps if step.get("reason") == "cap"]
        discovered = [f"p{i}" for i in reversed(indexes)]
        self.assertEqual(runs, discovered[: checks.MAX_PROJECTS_PER_ECOSYSTEM])
        self.assertEqual(capped, discovered[checks.MAX_PROJECTS_PER_ECOSYSTEM :])
        text = checks.render_dependencies(self.rows(steps))
        self.assertIn("p1/node_modules", text)
        self.assertIn("were not installed", text)
        self.assertIn("p7/node_modules", text)

    def test_dependencies_section_in_the_prompt(self):
        none = checks.render_prompt("Check.", "diff")
        self.assertIn("Dependencies are not installed", none)
        self.assertIn("Read only paths inside the repository", none)
        some = checks.render_dependencies([("npm", "a/node_modules", "ok"), ("nuget", ".checks-deps/nuget", "failed")])
        self.assertIn("- npm: `a/node_modules`", some)
        self.assertIn("could not be installed: nuget", some)
        self.assertNotIn("compiled", some)
        hidden = checks.render_dependencies([("npm", "bad\nname/node_modules", "skipped", "cap")])
        self.assertNotIn("bad", hidden)
        self.assertIn("cannot be installed safely", hidden)

    def test_diff_comes_first_and_placeholders_in_it_stay_text(self):
        text = checks.render_prompt("CHECK BODY", "+ @@CHECK_BODY@@ in a diff")
        self.assertLess(text.index("## PR Diff"), text.index("## Check"))
        self.assertIn("+ @@CHECK_BODY@@ in a diff", text)
        self.assertEqual(text.count("CHECK BODY"), 1)


class InstallDepsTest(unittest.TestCase):
    """install-deps.sh with a fake docker that records what it was asked."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.workspace = root / "work"
        (self.workspace / "pkg" / "src").mkdir(parents=True)
        (self.workspace / "pkg" / "package-lock.json").write_text("{}")
        (self.workspace / ".git").mkdir()
        (self.workspace / ".nextcli-checks-base" / ".checks").mkdir(parents=True)
        self.bin = root / "bin"
        self.bin.mkdir()
        self.calls = root / "docker-calls"
        self.cache = root / "cache"
        timeout = self.bin / "timeout"
        # GNU timeout isn't on macOS: drop the options and the duration.
        timeout.write_text('#!/usr/bin/env bash\nwhile [[ "$1" == -* ]]; do shift; done\nshift\nexec "$@"\n')
        timeout.chmod(0o755)
        self.changed = Path("/tmp/changed-files.txt")
        self.results = Path("/tmp/checks-deps.txt")
        self.summary = root / "summary.md"

    def tearDown(self):
        self.tmp.cleanup()
        for path in (self.changed, self.results):
            path.unlink(missing_ok=True)

    def run_install(self, docker_exit: int = 0, mode: str = "auto"):
        docker = self.bin / "docker"
        docker.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$*\" >> '{self.calls}'\n"
            f"[ \"$1\" = run ] && exit {docker_exit}\n"
            "exit 0\n"
        )
        docker.chmod(0o755)
        self.changed.write_text("pkg/src/a.ts\nREADME.md\n")
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GITHUB_WORKSPACE": str(self.workspace),
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "CHECKS_INSTALL": mode,
            "CHECKS_DEPS_CACHE": str(self.cache),
            "PROVIDER_API_KEY": "sk-secret-value",
        }
        result = subprocess.run(
            ["bash", str(Path(__file__).parent / "install-deps.sh")],
            env=env, capture_output=True, text=True,
        )
        calls = self.calls.read_text().splitlines() if self.calls.exists() else []
        return result, calls

    def test_installs_in_a_container_without_secrets(self):
        result, calls = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        (run,) = [c for c in calls if c.startswith("run ")]
        self.assertIn("node:22", run)
        self.assertIn("--read-only", run)
        self.assertIn("--cap-drop=ALL", run)
        self.assertIn("--security-opt=no-new-privileges", run)
        self.assertIn("--pids-limit=256", run)
        self.assertIn(f"-v {self.workspace}:/work:ro", run)
        self.assertIn(f"-v {self.workspace}/pkg/node_modules:/work/pkg/node_modules", run)
        self.assertIn("-w /work/pkg", run)
        self.assertNotIn(".git:/work/.git", run)
        script, command = run.split(" -c ", 1)[1].split(" -- ", 1)
        self.assertIn("npm ci --ignore-scripts", command)
        self.assertNotIn("a.ts", script)
        self.assertNotIn("pkg", script)
        self.assertNotIn("package-lock", script)
        self.assertNotIn("sk-secret-value", run)
        self.assertNotIn("PROVIDER_API_KEY", run)
        self.assertNotIn("docker.sock", run)
        self.assertEqual(self.results.read_text(), "npm\tpkg/node_modules\tok\t\n")
        self.assertEqual((self.workspace / ".checks-deps" / ".gitignore").read_text(), "*\n")
        self.assertIn("installed in", self.summary.read_text())

    def test_a_failed_install_is_a_warning(self):
        result, _calls = self.run_install(docker_exit=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::warning::Could not install npm", result.stdout)
        self.assertEqual(self.results.read_text(), "npm\tpkg/node_modules\tfailed\t\n")

    def test_yarn_classic_prepares_yarn_1_without_putting_the_project_in_the_shell(self):
        (self.workspace / "pkg" / "package-lock.json").unlink()
        (self.workspace / "pkg" / "yarn.lock").write_text("# yarn lockfile v1\n")
        result, calls = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        (run,) = [c for c in calls if c.startswith("run ")]
        script, command = run.split(" -c ", 1)[1].split('exec "$@" -- ', 1)
        self.assertIn("corepack prepare yarn@1.22.22 --activate", script)
        self.assertIn("--ignore-scripts", command)
        self.assertNotIn("a.ts", script)
        self.assertNotIn("yarn.lock", script)
        self.assertNotIn("pkg", script)

    def test_install_none_runs_nothing(self):
        result, calls = self.run_install(mode="none")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])
        self.assertEqual(self.results.read_text(), "")


class NextcliAgentArgsTest(unittest.TestCase):
    """nextcli.sh passes the turn limit to cn."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bin = root / "bin"
        self.bin.mkdir()
        self.args = root / "args"
        cn = self.bin / "cn"
        cn.write_text(f"#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > '{self.args}'\n")
        cn.chmod(0o755)
        self.prompt = root / "prompt.md"
        self.prompt.write_text("Check.")

    def tearDown(self):
        self.tmp.cleanup()

    def run_agent(self, max_turns: str) -> list[str]:
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "PROVIDER_API_KEY": "k",
            "CHECKS_PROVIDER": "openai-compatible",
            "CHECKS_MODEL": "m",
            "CHECKS_BASE_URL": "https://example.invalid/v1",
            "CHECKS_MAX_TURNS": max_turns,
        }
        result = subprocess.run(
            ["bash", str(Path(__file__).parent / "agents" / "nextcli.sh"), str(self.prompt)],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.args.read_text().splitlines()

    def test_turn_limit_is_passed(self):
        args = self.run_agent("60")
        self.assertEqual(args[args.index("--max-turns") + 1], "60")

    def test_zero_turns_means_no_limit(self):
        self.assertNotIn("--max-turns", self.run_agent("0"))
        self.assertNotIn("--max-turns", self.run_agent(""))


if __name__ == "__main__":
    unittest.main()
