#!/usr/bin/env python3
"""Helpers for discovering checks, rendering prompts, and reading agent output.

The shell entry points in this directory call subcommands of this file so the
workflow YAML stays thin and the JSON handling stays testable.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from pathlib import Path
from posixpath import basename

SCRIPT_DIR = Path(__file__).resolve().parent
KNOWN_PROVIDERS = (
    "anthropic",
    "openai",
    "deepseek",
    "openrouter",
    "openai-compatible",
)
FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?", re.S)
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)

READ_ONLY_BASH = {
    "*": "deny",
    "git diff*": "allow",
    "git log*": "allow",
    "git status*": "allow",
    "git show*": "allow",
    "git grep*": "allow",
    "rg*": "allow",
    "grep*": "allow",
    "ls*": "allow",
    "cat*": "allow",
    "head*": "allow",
    "tail*": "allow",
    "wc*": "allow",
    "jq*": "allow",
}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse the small YAML subset check files use. No PyYAML dependency."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    data: dict = {}
    lines = match.group(1).splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        key_only = re.match(r"^([A-Za-z0-9_-]+):\s*$", line)
        if key_only:
            key = key_only.group(1)
            items: list[str] = []
            index += 1
            while index < len(lines) and re.match(r"^\s+-\s+", lines[index]):
                item = re.sub(r"^\s+-\s+", "", lines[index]).strip()
                items.append(item.strip("\"'"))
                index += 1
            data[key] = items
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip().strip("\"'")
        index += 1
    return data, text[match.end() :]


def split_model(value: str, default_provider: str) -> tuple[str, str]:
    """Split provider/model when the prefix is a known provider.

    OpenRouter model ids contain slashes (`anthropic/claude-sonnet-4-5`).
    Only the first segment is treated as a provider, and only when it is one
    of the providers this action supports. `openrouter/anthropic/claude-sonnet-4-5`
    selects OpenRouter and keeps `anthropic/claude-sonnet-4-5` as the model id.
    """
    if "/" in value:
        prefix, rest = value.split("/", 1)
        if prefix in KNOWN_PROVIDERS and rest:
            return prefix, rest
    return default_provider, value


def path_matches(path: str, pattern: str) -> bool:
    posix = path.replace("\\", "/")
    if Path(posix).match(pattern):
        return True
    if fnmatch.fnmatch(posix, pattern):
        return True
    if pattern.startswith("**/"):
        rest = pattern[3:]
        return fnmatch.fnmatch(posix, rest) or fnmatch.fnmatch(basename(posix), rest)
    return False


def check_applies(frontmatter: dict, changed_files: list[str] | None) -> bool:
    """A check with no paths always applies. Unknown diffs do not skip checks."""
    paths = frontmatter.get("paths")
    if not paths:
        return True
    if changed_files is None:
        return True
    return any(
        path_matches(changed, pattern)
        for changed in changed_files
        for pattern in paths
    )


def load_changed_files(path: str | None) -> list[str] | None:
    if not path:
        return None
    file = Path(path)
    if not file.is_file():
        return None
    return [line.strip() for line in file.read_text().splitlines() if line.strip()]


def iter_checks(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.md") if path.is_file())


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_prompt(check_body: str, diff: str, retry: bool = False) -> str:
    template = (SCRIPT_DIR / "prompt.md").read_text()
    text = template.replace("@@CHECK_BODY@@", check_body).replace("@@PR_DIFF@@", diff)
    if retry:
        text += (
            "\n\nYour previous response was not valid JSON matching the schema. "
            "Reply with only the JSON object, with no markdown fences and no other text.\n"
        )
    return text


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = FENCE_RE.sub("", stripped).strip()
    return stripped


def _balanced_objects(text: str) -> list[object]:
    found: list[object] = []
    start_at = 0
    while True:
        start = text.find("{", start_at)
        if start < 0:
            return found
        depth = 0
        in_string = False
        escaped = False
        closed = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[start : index + 1]
                    try:
                        found.append(json.loads(chunk))
                    except json.JSONDecodeError:
                        pass
                    start_at = index + 1
                    closed = True
                    break
        if not closed:
            return found


def _text_from_event(event: dict) -> str:
    part = event.get("part")
    if isinstance(part, dict) and isinstance(part.get("text"), str):
        return part["text"]
    for key in ("text", "content", "result", "response"):
        value = event.get(key)
        if isinstance(value, str):
            return value
    return ""


def _candidate_texts(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        return []
    texts = [raw]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        for key in ("result", "response", "content"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                texts.append(value)
    else:
        chunks: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                piece = _text_from_event(event)
                if piece:
                    chunks.append(piece)
                texts.append(line)
        if chunks:
            texts.append("\n".join(chunks))
    return texts


def _normalize_suggestion(item: object) -> dict | None:
    if not isinstance(item, dict):
        return None
    file_name = item.get("file")
    line = item.get("line")
    replacement = item.get("replacement")
    if not isinstance(file_name, str) or not file_name:
        return None
    if replacement is None:
        return None
    try:
        line_number = int(line)
    except (TypeError, ValueError):
        return None
    start = item.get("start_line", line_number)
    try:
        start_number = int(start) if start is not None else line_number
    except (TypeError, ValueError):
        start_number = line_number
    if start_number > line_number or start_number < 1 or line_number < 1:
        start_number = line_number
    comment = item.get("comment")
    return {
        "file": file_name,
        "start_line": start_number,
        "line": line_number,
        "replacement": str(replacement),
        "comment": "" if comment is None else str(comment),
    }


def normalize_verdict(obj: object) -> dict | None:
    if not isinstance(obj, dict):
        return None
    verdict = obj.get("verdict")
    if verdict not in ("PASS", "FAIL"):
        return None
    reason = obj.get("reason")
    suggestions_in = obj.get("suggestions") or []
    if not isinstance(suggestions_in, list):
        return None
    suggestions = []
    for item in suggestions_in:
        normalized = _normalize_suggestion(item)
        if normalized:
            suggestions.append(normalized)
    if verdict == "PASS":
        suggestions = []
    return {
        "verdict": verdict,
        "reason": "No reason given" if not isinstance(reason, str) or not reason.strip() else reason.strip(),
        "suggestions": suggestions,
    }


def parse_verdict(raw: str) -> dict | None:
    """Return the last valid verdict object found in agent output."""
    found: dict | None = None
    for text in _candidate_texts(raw):
        cleaned = _strip_fences(text)
        for obj in _balanced_objects(cleaned):
            normalized = normalize_verdict(obj)
            if normalized:
                found = normalized
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            parsed = None
        normalized = normalize_verdict(parsed)
        if normalized:
            found = normalized
    return found


def opencode_config(provider: str, model: str, api_key: str, base_url: str) -> dict:
    options: dict[str, str] = {"apiKey": api_key}
    if base_url:
        options["baseURL"] = base_url
    entry: dict = {"options": options}
    if provider == "openai-compatible":
        entry["npm"] = "@ai-sdk/openai-compatible"
        entry["name"] = "OpenAI-compatible"
        entry["models"] = {model: {"name": model}}
    return {
        "$schema": "https://opencode.ai/config.json",
        "provider": {provider: entry},
        "permission": {
            "edit": "deny",
            "write": "deny",
            "bash": READ_ONLY_BASH,
        },
    }


def nextcli_config(provider: str, model: str, api_key: str, base_url: str) -> str:
    mapped = "openai" if provider == "openai-compatible" else provider
    lines = [
        "name: AI Checks",
        "version: 1.0.0",
        "models:",
        "  - name: checks",
        f"    provider: {mapped}",
        f"    model: {yaml_quote(model)}",
        f"    apiKey: {yaml_quote(api_key)}",
    ]
    if base_url:
        lines.append(f"    apiBase: {yaml_quote(base_url)}")
    lines.extend(["    roles:", "      - chat"])
    return "\n".join(lines) + "\n"


def review_payload(name: str, sha: str, verdict: dict) -> dict:
    comments = []
    for suggestion in verdict.get("suggestions") or []:
        comment = {
            "path": suggestion["file"],
            "side": "RIGHT",
            "body": (
                f"**check/{name}**\n\n{suggestion['comment']}\n\n"
                f"```suggestion\n{suggestion['replacement']}\n```"
            ),
        }
        if suggestion["start_line"] == suggestion["line"]:
            comment["line"] = suggestion["line"]
        else:
            comment["start_line"] = suggestion["start_line"]
            comment["line"] = suggestion["line"]
        comments.append(comment)
    return {
        "event": "REQUEST_CHANGES",
        "body": f"## check/{name}\n\n{verdict.get('reason', '')}",
        "commit_id": sha,
        "comments": comments,
    }


def check_run_body(verdict: dict, conclusion: str, details_url: str) -> dict:
    reason = str(verdict.get("reason") or "No reason given")
    body: dict = {
        "status": "completed",
        "conclusion": conclusion,
        "output": {
            "title": reason[:140],
            "summary": reason,
        },
    }
    if details_url:
        body["details_url"] = details_url
    return body


def conclusion_for(verdict: str, on_error: str) -> str:
    if verdict == "PASS" or verdict == "SKIP":
        return "success"
    if verdict == "FAIL":
        return "failure"
    if on_error == "failure":
        return "failure"
    return "neutral"


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} is empty.")
    return value


def _read_verdict_file(path: str | None) -> dict:
    raw = sys.stdin.read() if not path else Path(path).read_text()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Verdict file is not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("Verdict file must be a JSON object.")
    return parsed


def cmd_list(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    names = [path.stem for path in iter_checks(directory)]
    if args.lines:
        sys.stdout.write("".join(f"{name}\n" for name in names))
    else:
        json.dump(names, sys.stdout)
        sys.stdout.write("\n")
    return 0


def cmd_frontmatter(args: argparse.Namespace) -> int:
    text = Path(args.check).read_text()
    frontmatter, _body = parse_frontmatter(text)
    changed = load_changed_files(args.changed)
    applies = check_applies(frontmatter, changed)
    title = str(frontmatter.get("name") or Path(args.check).stem)
    sys.stdout.write(f"fm_skip={0 if applies else 1}\n")
    sys.stdout.write(f"fm_title={shell_quote(title)}\n")
    model_value = frontmatter.get("model")
    if isinstance(model_value, str) and model_value:
        provider, model = split_model(model_value, args.provider or "")
        if provider:
            sys.stdout.write(f"fm_provider={shell_quote(provider)}\n")
        sys.stdout.write(f"fm_model={shell_quote(model)}\n")
    return 0


def cmd_render_prompt(args: argparse.Namespace) -> int:
    text = Path(args.check).read_text()
    _frontmatter, body = parse_frontmatter(text)
    diff = Path(args.diff).read_text() if args.diff else ""
    sys.stdout.write(render_prompt(body, diff, retry=args.retry))
    return 0


def cmd_parse_verdict(_args: argparse.Namespace) -> int:
    parsed = parse_verdict(sys.stdin.read())
    if parsed is None:
        return 1
    json.dump(parsed, sys.stdout)
    sys.stdout.write("\n")
    return 0


def cmd_opencode_config(_args: argparse.Namespace) -> int:
    provider = _env("CHECKS_PROVIDER")
    model = _env("CHECKS_MODEL")
    api_key = _env("PROVIDER_API_KEY")
    base_url = os.environ.get("CHECKS_BASE_URL", "")
    if provider not in KNOWN_PROVIDERS:
        raise SystemExit(
            f"Unsupported provider '{provider}'. "
            f"Use one of: {', '.join(KNOWN_PROVIDERS)}."
        )
    if provider == "openai-compatible" and not base_url:
        raise SystemExit("base-url is required when provider is openai-compatible.")
    json.dump(opencode_config(provider, model, api_key, base_url), sys.stdout)
    sys.stdout.write("\n")
    return 0


def cmd_nextcli_config(_args: argparse.Namespace) -> int:
    provider = _env("CHECKS_PROVIDER")
    model = _env("CHECKS_MODEL")
    api_key = _env("PROVIDER_API_KEY")
    base_url = os.environ.get("CHECKS_BASE_URL", "")
    if provider not in KNOWN_PROVIDERS:
        raise SystemExit(
            f"Unsupported provider '{provider}'. "
            f"Use one of: {', '.join(KNOWN_PROVIDERS)}."
        )
    if provider == "openai-compatible" and not base_url:
        raise SystemExit("base-url is required when provider is openai-compatible.")
    sys.stdout.write(nextcli_config(provider, model, api_key, base_url))
    return 0


def cmd_review_payload(args: argparse.Namespace) -> int:
    verdict = _read_verdict_file(args.verdict)
    json.dump(review_payload(args.name, args.sha, verdict), sys.stdout)
    sys.stdout.write("\n")
    return 0


def cmd_check_run_body(args: argparse.Namespace) -> int:
    verdict = _read_verdict_file(args.verdict)
    json.dump(
        check_run_body(verdict, args.conclusion, args.details_url or ""),
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI check helpers")
    sub = parser.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("--dir", required=True)
    list_cmd.add_argument("--lines", action="store_true")
    list_cmd.set_defaults(func=cmd_list)

    front = sub.add_parser("frontmatter")
    front.add_argument("--check", required=True)
    front.add_argument("--changed")
    front.add_argument("--provider", default="")
    front.set_defaults(func=cmd_frontmatter)

    render = sub.add_parser("render-prompt")
    render.add_argument("--check", required=True)
    render.add_argument("--diff", required=True)
    render.add_argument("--retry", action="store_true")
    render.set_defaults(func=cmd_render_prompt)

    parse = sub.add_parser("parse-verdict")
    parse.set_defaults(func=cmd_parse_verdict)

    opencode = sub.add_parser("opencode-config")
    opencode.set_defaults(func=cmd_opencode_config)

    nextcli = sub.add_parser("nextcli-config")
    nextcli.set_defaults(func=cmd_nextcli_config)

    review = sub.add_parser("review-payload")
    review.add_argument("--name", required=True)
    review.add_argument("--sha", required=True)
    review.add_argument("--verdict")
    review.set_defaults(func=cmd_review_payload)

    check_run = sub.add_parser("check-run-body")
    check_run.add_argument("--conclusion", required=True)
    check_run.add_argument("--details-url", default="")
    check_run.add_argument("--verdict")
    check_run.set_defaults(func=cmd_check_run_body)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
