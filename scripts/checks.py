#!/usr/bin/env python3
"""Helpers for discovering checks, rendering prompts, and reading agent output.

The shell entry points in this directory call subcommands of this file so the
workflow YAML stays thin and the JSON handling stays testable.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import math
import os
import re
import sys
from pathlib import Path
from posixpath import basename
from urllib.parse import quote

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


def render_prompt(
    check_body: str,
    diff: str,
    retry: bool = False,
    empty_review: bool = False,
    dependencies: str = "",
) -> str:
    template = (SCRIPT_DIR / "prompt.md").read_text()
    values = {
        "PR_DIFF": diff,
        "DEPENDENCIES": dependencies or render_dependencies([]),
        "CHECK_BODY": check_body,
    }
    # One pass, so a placeholder written inside the diff is left as text.
    text = re.sub(r"@@(PR_DIFF|DEPENDENCIES|CHECK_BODY)@@", lambda m: values[m.group(1)], template)
    if empty_review:
        text += (
            "\n\nYour previous response did not review the diff in this prompt. "
            "A path you could not read is not an empty pull request. "
            "Review the diff above and reply with only the JSON object.\n"
        )
    elif retry:
        text += (
            "\n\nYour previous response was not valid JSON matching the schema. "
            "Reply with only the JSON object, with no markdown fences and no other text.\n"
        )
    return text


# Where install-deps.sh puts the dependencies of ecosystems whose packages
# would otherwise land outside the repository. It is inside the workspace, so
# the agent may read it, and holds a .gitignore of "*", so Search skips it.
DEPS_DIR = ".checks-deps"

# Paths that are never a project to install: dependencies and the checks'
# own base checkout.
_NOT_A_PROJECT = ("node_modules", DEPS_DIR, ".nextcli-checks-base", ".git")


def _ancestors(root: Path, rel_dir: str) -> list[str]:
    """rel_dir and each of its parents up to the root ("" is the root)."""
    parts = [p for p in rel_dir.split("/") if p and p != "."]
    return ["/".join(parts[:i]) for i in range(len(parts), -1, -1)]


def _has(root: Path, rel_dir: str, *names: str) -> str | None:
    base = root / rel_dir if rel_dir else root
    for name in names:
        if "*" in name:
            found = sorted(base.glob(name))
            if found:
                return found[0].name
        elif (base / name).is_file():
            return name
    return None


def _major_version(text: str) -> str | None:
    match = re.search(r"(\d+)", text)
    return match.group(1) if match else None


def _node_image(root: Path, rel_dir: str) -> str:
    for d in _ancestors(root, rel_dir):
        base = root / d if d else root
        for name in (".nvmrc", ".node-version"):
            if (base / name).is_file():
                major = _major_version((base / name).read_text())
                if major:
                    return f"node:{major}"
        if (base / "package.json").is_file():
            try:
                engines = json.loads((base / "package.json").read_text()).get("engines") or {}
            except (json.JSONDecodeError, AttributeError):
                engines = {}
            major = _major_version(str(engines.get("node") or ""))
            if major:
                return f"node:{major}"
    return "node:22"


def _dotnet_image(root: Path, rel_dir: str) -> str:
    for d in _ancestors(root, rel_dir):
        path = (root / d if d else root) / "global.json"
        if path.is_file():
            try:
                version = str(json.loads(path.read_text())["sdk"]["version"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            match = re.match(r"(\d+\.\d+)", version)
            if match:
                return f"mcr.microsoft.com/dotnet/sdk:{match.group(1)}"
    return "mcr.microsoft.com/dotnet/sdk:10.0"


def _go_image(root: Path, rel_dir: str) -> str:
    go_mod = (root / rel_dir if rel_dir else root) / "go.mod"
    match = re.search(r"^go (\d+\.\d+)", go_mod.read_text(), re.M) if go_mod.is_file() else None
    return f"golang:{match.group(1)}" if match else "golang:1"


# The JavaScript managers install into the project's own node_modules, which
# is where Node looks and where the agent will look. Lifecycle scripts are off:
# the dependencies are there to be read, not run.
_COREPACK = (
    "mkdir -p /tmp/home/bin && "
    "{ corepack enable --install-directory /tmp/home/bin 2>/dev/null || "
    "npx --yes corepack enable --install-directory /tmp/home/bin; } && "
    "export PATH=/tmp/home/bin:$PATH && "
)


def _js_step(root: Path, rel_dir: str, lockfile: str) -> dict:
    if lockfile == "pnpm-lock.yaml":
        manager, command = "pnpm", _COREPACK + "pnpm install --frozen-lockfile --ignore-scripts --store-dir /cache/pnpm"
    elif lockfile == "yarn.lock":
        lock = (root / rel_dir / lockfile) if rel_dir else root / lockfile
        if "__metadata:" in lock.read_text(errors="replace")[:2000]:
            manager, command = "yarn", _COREPACK + "yarn install --immutable --mode=skip-build"
        else:
            manager, command = "yarn", "yarn install --frozen-lockfile --ignore-scripts --non-interactive"
    else:
        manager, command = "npm", "npm ci --ignore-scripts --no-audit --no-fund"
    return {
        "ecosystem": manager,
        "image": _node_image(root, rel_dir),
        "command": command,
        "env": {
            "npm_config_cache": "/cache/npm",
            "YARN_CACHE_FOLDER": "/cache/yarn",
            "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
        },
        "deps": f"{rel_dir}/node_modules" if rel_dir else "node_modules",
    }


def _nearest(root: Path, rel_dir: str, names: tuple[str, ...]) -> tuple[str, str] | None:
    for d in _ancestors(root, rel_dir):
        found = _has(root, d, *names)
        if found:
            return d, found
    return None


def _outermost(root: Path, rel_dir: str, names: tuple[str, ...]) -> tuple[str, str] | None:
    found = None
    for d in _ancestors(root, rel_dir):
        name = _has(root, d, *names)
        if name:
            found = (d, name)
    return found


# A changed file only asks for the ecosystems its kind of file belongs to, so
# a README or a Python helper in a TypeScript repository installs nothing extra.
_ECOSYSTEM_FILES = {
    "js": re.compile(r"\.(?:[cm]?[jt]sx?|vue|svelte|astro)$|(?:^|/)(?:package\.json|package-lock\.json|npm-shrinkwrap\.json|pnpm-lock\.yaml|yarn\.lock|tsconfig[^/]*\.json)$"),
    "nuget": re.compile(r"\.(?:cs|fs|fsx|vb|razor|cshtml|xaml|csproj|fsproj|vbproj|sln|slnx|props|targets)$|(?:^|/)(?:packages\.lock\.json|global\.json|nuget\.config)$", re.I),
    "pip": re.compile(r"\.pyi?$|(?:^|/)(?:requirements[^/]*\.txt|pyproject\.toml|setup\.py|setup\.cfg)$"),
    "go": re.compile(r"\.go$|(?:^|/)go\.(?:mod|sum)$"),
    "cargo": re.compile(r"\.rs$|(?:^|/)Cargo\.(?:toml|lock)$"),
    "jvm": re.compile(r"\.(?:java|kt|kts|scala|groovy)$|(?:^|/)(?:pom\.xml|build\.gradle(?:\.kts)?|settings\.gradle(?:\.kts)?|gradle\.properties)$"),
}


def file_ecosystems(path: str) -> set[str]:
    return {kind for kind, pattern in _ECOSYSTEM_FILES.items() if pattern.search(path)}


def _steps_for(root: Path, rel_dir: str, kinds: set[str]) -> list[dict]:
    """The install steps for the projects that contain rel_dir, for the given
    kinds of file."""
    steps: list[dict] = []
    js = "js" in kinds and _nearest(root, rel_dir, ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock"))
    if js:
        steps.append({"dir": js[0], **_js_step(root, js[0], js[1])})

    dotnet = "nuget" in kinds and _nearest(root, rel_dir, ("*.sln", "*.slnx", "*.csproj", "*.fsproj", "*.vbproj"))
    if dotnet:
        # A solution restores every project in it; otherwise restore the
        # nearest project, which brings its project references along.
        target = _has(root, dotnet[0], "*.sln", "*.slnx") or dotnet[1]
        steps.append({
            "dir": dotnet[0],
            "ecosystem": "nuget",
            "image": _dotnet_image(root, dotnet[0]),
            "command": f"dotnet restore '{target}'",
            "env": {
                "NUGET_PACKAGES": f"/work/{DEPS_DIR}/nuget",
                "NUGET_HTTP_CACHE_PATH": "/cache/nuget-http",
                "DOTNET_CLI_HOME": "/tmp/home",
                "DOTNET_NOLOGO": "1",
                "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
                "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
            },
            "deps": f"{DEPS_DIR}/nuget",
        })

    python = "pip" in kinds and _nearest(root, rel_dir, ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg"))
    if python:
        source = "-r requirements.txt" if _has(root, python[0], "requirements.txt") else "."
        steps.append({
            "dir": python[0],
            "ecosystem": "pip",
            "image": "python:3.13",
            "command": f"pip install --target /work/{DEPS_DIR}/python {source}",
            "env": {
                "PIP_CACHE_DIR": "/cache/pip",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_NO_INPUT": "1",
            },
            "deps": f"{DEPS_DIR}/python",
        })

    go = "go" in kinds and _nearest(root, rel_dir, ("go.mod",))
    if go:
        steps.append({
            "dir": go[0],
            "ecosystem": "go",
            "image": _go_image(root, go[0]),
            "command": "go mod download",
            "env": {
                "GOMODCACHE": f"/work/{DEPS_DIR}/go",
                "GOFLAGS": "-modcacherw",
                "GOCACHE": "/tmp/home/gocache",
                "GOPATH": "/tmp/home/go",
            },
            "deps": f"{DEPS_DIR}/go",
        })

    cargo = "cargo" in kinds and (_nearest(root, rel_dir, ("Cargo.lock",)) or _nearest(root, rel_dir, ("Cargo.toml",)))
    if cargo:
        steps.append({
            "dir": cargo[0],
            "ecosystem": "cargo",
            "image": "rust:1",
            "command": "cargo fetch",
            "env": {"CARGO_HOME": f"/work/{DEPS_DIR}/cargo"},
            "deps": f"{DEPS_DIR}/cargo/registry/src",
        })

    # Maven and Gradle build from the root of a multi-module build.
    maven = "jvm" in kinds and _outermost(root, rel_dir, ("pom.xml",))
    if maven:
        steps.append({
            "dir": maven[0],
            "ecosystem": "maven",
            "image": "maven:3-eclipse-temurin-21",
            "command": f"mvn -B -q dependency:go-offline -Dmaven.repo.local=/work/{DEPS_DIR}/maven -Duser.home=/tmp/home",
            "env": {"MAVEN_CONFIG": "/tmp/home/.m2"},
            "deps": f"{DEPS_DIR}/maven",
        })
    gradle = "jvm" in kinds and (
        _outermost(root, rel_dir, ("settings.gradle", "settings.gradle.kts"))
        or _nearest(root, rel_dir, ("build.gradle", "build.gradle.kts"))
    )
    if gradle:
        runner = "./gradlew" if _has(root, gradle[0], "gradlew") else "gradle"
        steps.append({
            "dir": gradle[0],
            "ecosystem": "gradle",
            "image": "gradle:jdk21",
            "command": f"{runner} --no-daemon -q dependencies",
            "env": {"GRADLE_USER_HOME": f"/work/{DEPS_DIR}/gradle"},
            "deps": f"{DEPS_DIR}/gradle",
        })
    return steps


# More projects than this per ecosystem is a sweeping change; installing them
# all would take longer than the check.
MAX_PROJECTS_PER_ECOSYSTEM = 6


def deps_plan(root: Path, changed: list[str]) -> list[dict]:
    """One install step per (ecosystem, project directory) that holds a
    changed file, nearest project first, in a stable order."""
    seen: dict[tuple[str, str], dict] = {}
    for path in changed:
        path = path.strip()
        if not path or any(part in _NOT_A_PROJECT for part in path.split("/")):
            continue
        kinds = file_ecosystems(path)
        if not kinds:
            continue
        for step in _steps_for(root, os.path.dirname(path), kinds):
            seen.setdefault((step["ecosystem"], step["dir"]), step)
    plan: list[dict] = []
    per_ecosystem: dict[str, int] = {}
    for (ecosystem, _dir), step in sorted(seen.items()):
        per_ecosystem[ecosystem] = per_ecosystem.get(ecosystem, 0) + 1
        if per_ecosystem[ecosystem] <= MAX_PROJECTS_PER_ECOSYSTEM:
            plan.append(step)
    return plan


def render_dependencies(results: list[tuple[str, str, str]]) -> str:
    """The prompt's Dependencies section, from install-deps.sh's
    (ecosystem, location, status) lines."""
    installed = [(e, loc) for e, loc, status in results if status == "ok"]
    failed = [(e, loc) for e, loc, status in results if status != "ok"]
    lines: list[str] = []
    if installed:
        lines.append("The pull request's dependencies are installed inside the repository:")
        lines.extend(f"- {e}: `{loc}`" for e, loc in installed)
        lines.append("Read a library there when you need its behavior or types.")
        if any(e in ("nuget", "maven", "gradle") for e, _loc in installed):
            lines.append(
                "NuGet, Maven and Gradle packages are compiled: their API is in the documentation and metadata files next to the binaries."
            )
    if failed:
        lines.append(
            "These could not be installed: "
            + ", ".join(f"{e} (`{loc}`)" for e, loc in failed)
            + ". Judge those libraries from how the repository uses them."
        )
    if not lines:
        lines.append(
            "Dependencies are not installed. Judge a library from how the repository uses it."
        )
    lines.append(
        "Read only paths inside the repository. Do not look for packages in caches or system folders."
    )
    return "\n".join(lines)


def read_dependency_results(path: str) -> list[tuple[str, str, str]]:
    results = []
    file = Path(path)
    if not file.is_file():
        return results
    for line in file.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            results.append((parts[0], parts[1], parts[2]))
    return results


def reviewed_nothing(verdict: dict, diff: str) -> bool:
    """A finished-looking verdict that checked no file while a diff was given."""
    if not diff.strip():
        return False
    if verdict.get("complete") is not False:
        return False
    return not verdict.get("findings")


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


def _positive_int(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None


def _first_sentence(text: str, limit: int = 80) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    first = re.split(r"(?<=[.!?])\s", first, maxsplit=1)[0]
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def _normalize_finding(item: object) -> dict | None:
    """One finding: a file, an optional line, a title and markdown details.

    Also accepts the v2 suggestion format (comment and replacement), which
    opencode and claude-code checks written for v2 may still produce.
    """
    if not isinstance(item, dict):
        return None
    file_name = item.get("file")
    if not isinstance(file_name, str) or not file_name.strip():
        return None
    line = _positive_int(item.get("line"))
    start_line = _positive_int(item.get("start_line"))
    if start_line is not None and (line is None or start_line >= line):
        start_line = None

    details = item.get("details")
    if not isinstance(details, str):
        comment = item.get("comment")
        replacement = item.get("replacement")
        parts = []
        if isinstance(comment, str) and comment.strip():
            parts.append(comment.strip())
        if replacement is not None:
            parts.append(f"Suggested code:\n\n```\n{replacement}\n```")
        details = "\n\n".join(parts)
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        title = _first_sentence(details) or "Finding"

    finding = {
        "file": file_name.strip(),
        "line": line,
        "title": title.strip(),
        "details": details.strip(),
    }
    if start_line is not None:
        finding["start_line"] = start_line
    return finding


def normalize_verdict(obj: object) -> dict | None:
    if not isinstance(obj, dict):
        return None
    verdict = obj.get("verdict")
    # Models often write "pass" or "Fail"; the value is the same.
    if isinstance(verdict, str):
        verdict = verdict.strip().upper()
    if verdict not in ("PASS", "FAIL"):
        return None
    summary = obj.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        summary = obj.get("reason")
    findings_in = obj.get("findings")
    if findings_in is None:
        findings_in = obj.get("suggestions")
    if findings_in is None:
        findings_in = []
    if not isinstance(findings_in, list):
        return None
    findings = [f for f in (_normalize_finding(item) for item in findings_in) if f]
    if verdict == "PASS":
        findings = []
    return {
        "verdict": verdict,
        "summary": (
            summary.strip()
            if isinstance(summary, str) and summary.strip()
            else "No summary given"
        ),
        "complete": obj.get("complete") is not False,
        "findings": findings,
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


def nextcli_config(
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
    max_output_tokens: int | None = None,
) -> str:
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
    # Without it the provider's default applies; DeepSeek's 4,096 output tokens
    # can be spent on thinking before the answer is written.
    if max_output_tokens:
        lines.extend(
            ["    defaultCompletionOptions:", f"      maxTokens: {max_output_tokens}"]
        )
    return "\n".join(lines) + "\n"


COMMENT_MARKER = "<!-- nextcli-checks -->"
# GitHub rejects comments and check-run summaries over 65,536 characters
GITHUB_TEXT_LIMIT = 65_536
TRUNCATION_NOTE = "\n\n_The results were cut to fit in a comment. See the check runs for the rest._\n"
MENTION_RE = re.compile(r"(?<![\w`])@(?=[A-Za-z0-9])")
VERDICT_LABELS = {
    "PASS": "Passed",
    "FAIL": "Failed",
    "SKIP": "Skipped",
    "ERROR": "Error",
    "INCOMPLETE": "Incomplete",
}


def _neutralize_mentions(text: str) -> str:
    """Stop @name in model output from notifying people. Code is left as is."""
    parts = re.split(r"(```.*?```|`[^`\n]*`)", text, flags=re.S)
    return "".join(
        part if part.startswith("`") else MENTION_RE.sub("@​", part)
        for part in parts
    )


def _is_safe_relative_path(path: str) -> bool:
    return (
        not path.startswith(("/", "\\"))
        and ".." not in path.replace("\\", "/").split("/")
        and "://" not in path
    )


def finding_location(finding: dict, repo: str, sha: str) -> str:
    """`file:line`, linked to the file at the reviewed commit when possible."""
    path = finding["file"]
    line = finding.get("line")
    start = finding.get("start_line")
    label = path
    anchor = ""
    if line:
        label = f"{path}:{start}-{line}" if start else f"{path}:{line}"
        anchor = f"#L{start}-L{line}" if start else f"#L{line}"
    label_md = f"`{label.replace('`', '')}`"
    if not repo or not sha or not _is_safe_relative_path(path):
        return label_md
    url = f"https://github.com/{repo}/blob/{sha}/{quote(path.lstrip('./'), safe='/')}{anchor}"
    return f"[{label_md}]({url})"


def _format_cost(usage: dict | None) -> str:
    if not isinstance(usage, dict) or not isinstance(usage.get("cost"), (int, float)):
        return ""
    cost = f"${usage['cost']:.2f}"
    if usage.get("priced") not in (None, "exact"):
        cost += " (estimated)"
    return cost


def partial_note(verdict: dict) -> str:
    if verdict.get("verdict") != "INCOMPLETE" and verdict.get("complete") is not False:
        return ""
    if verdict.get("limit") == "cost":
        why = "It reached its cost limit before it finished"
    elif verdict.get("limit") in ("turns", "tokens"):
        why = f"It reached its {verdict['limit'].rstrip('s')} limit before it finished"
    else:
        why = "The agent did not finish"
    return f"> **Partial review.** {why}. The findings cover only what it checked.\n"


def render_findings(verdict: dict, repo: str, sha: str) -> str:
    lines = []
    for number, finding in enumerate(verdict.get("findings") or [], start=1):
        lines.append(f"**{number}. {finding['title']}**")
        lines.append(finding_location(finding, repo, sha))
        if finding.get("details"):
            lines.append("")
            lines.append(finding["details"])
        lines.append("")
    return "\n".join(lines)


def render_check_section(name: str, verdict: dict, repo: str, sha: str) -> str:
    label = VERDICT_LABELS.get(verdict.get("verdict", ""), verdict.get("verdict", ""))
    count = len(verdict.get("findings") or [])
    heading = f"### `{name}`: {label}"
    if count:
        heading += f", {count} finding{'s' if count != 1 else ''}"
    parts = [heading, "", verdict.get("summary") or ""]
    note = partial_note(verdict)
    if note:
        parts += ["", note]
    findings = render_findings(verdict, repo, sha)
    if findings:
        parts += ["", findings]
    return "\n".join(parts).rstrip() + "\n"


def _fit(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(TRUNCATION_NOTE)].rstrip() + TRUNCATION_NOTE


def render_comment(
    results: list[tuple[str, dict]], repo: str, sha: str, max_cost: str = ""
) -> str:
    """The single pull request comment that holds every check's result."""
    header = [COMMENT_MARKER, "## AI review", ""]
    facts = [f"Commit `{sha[:7]}`" if sha else "", f"{len(results)} check{'s' if len(results) != 1 else ''}"]
    costs = [v.get("usage", {}).get("cost") for _n, v in results if isinstance(v.get("usage"), dict)]
    costs = [c for c in costs if isinstance(c, (int, float))]
    if costs:
        estimated = any(
            (v.get("usage") or {}).get("priced") not in (None, "exact") for _n, v in results
        )
        cost = f"cost ${sum(costs):.2f}{' (estimated)' if estimated else ''}"
        if max_cost:
            cost += f", limit ${float(max_cost):.2f} per check"
        facts.append(cost)
    header.append(" · ".join(f for f in facts if f))
    header.append("")
    if not results:
        header.append("No check produced a result. See the workflow run for details.")
    body = "\n".join(header) + "\n"
    for name, verdict in sorted(results, key=lambda item: item[0]):
        body += "\n" + render_check_section(name, verdict, repo, sha)
    body = _neutralize_mentions(redact_text(body, secrets_from_env()))
    return _fit(body, GITHUB_TEXT_LIMIT)


def check_run_body(verdict: dict, conclusion: str, details_url: str, repo: str = "", sha: str = "") -> dict:
    summary = str(verdict.get("summary") or "No summary given")
    text_parts = [summary]
    note = partial_note(verdict)
    if note:
        text_parts.append(note)
    findings = render_findings(verdict, repo, sha)
    if findings:
        text_parts.append(findings)
    cost = _format_cost(verdict.get("usage"))
    if cost:
        text_parts.append(f"Cost: {cost}")
    body: dict = {
        "status": "completed",
        "conclusion": conclusion,
        "output": {
            "title": summary.splitlines()[0][:140],
            "summary": _fit(_neutralize_mentions("\n\n".join(text_parts)), GITHUB_TEXT_LIMIT),
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
    if verdict == "INCOMPLETE":
        return "neutral"
    if on_error == "failure":
        return "failure"
    return "neutral"


def read_run_summary(stderr_text: str) -> dict | None:
    """The agent's last stderr line when it is a NextCLI run summary or error."""
    for line in reversed(stderr_text.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("status") in ("done", "limit", "error"):
            return parsed
        return None
    return None


ERROR_DETAIL_LIMIT = 500


def _error_detail(run_summary: dict | None) -> str:
    """Why an agent run gave no verdict, from its last stderr line."""
    if not run_summary:
        return ""
    status = run_summary.get("status")
    if status == "error":
        message = str(run_summary.get("message") or "").strip()
        if message:
            return f"The agent stopped with an error: {_fit(message, ERROR_DETAIL_LIMIT)}"
        return "The agent stopped with an error."
    if status == "done":
        return "The agent finished without writing an answer."
    return ""


def annotate_verdict(
    verdict: dict,
    run_summary: dict | None,
    limit_exit: bool,
    spent_before: float = 0.0,
) -> dict:
    """Adds the agent's usage, says why an ERROR verdict has no result, and
    marks a run ended by a limit as partial. `spent_before` is what an earlier
    run of the same check cost, so the reported cost covers both runs."""
    annotated = dict(verdict)
    limit = None
    if run_summary:
        if isinstance(run_summary.get("usage"), dict):
            usage = dict(run_summary["usage"])
            cost = usage.get("cost")
            if spent_before and isinstance(cost, (int, float)):
                usage["cost"] = round(cost + spent_before, 6)
            annotated["usage"] = usage
        limit = run_summary.get("limit")
    detail = _error_detail(run_summary)
    if annotated.get("verdict") == "ERROR" and detail:
        annotated["summary"] = f"{annotated.get('summary') or ''} {detail}".strip()
    if limit_exit or limit:
        annotated["limit"] = limit or "cost"
        if annotated.get("verdict") in ("PASS", "FAIL"):
            annotated["complete"] = False
    return annotated


# Environment variables whose values must never appear in anything posted to
# the pull request or uploaded as an artifact.
SECRET_ENV_VARS = ("PROVIDER_API_KEY", "GH_TOKEN", "GITHUB_TOKEN")
REDACTED = "[REDACTED]"


def redact_text(text: str, secrets: list[str]) -> str:
    # Longest first, so a secret that contains another is replaced whole.
    for secret in sorted({s for s in secrets if len(s) >= 8}, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    return text


def secrets_from_env() -> list[str]:
    return [os.environ.get(name, "") for name in SECRET_ENV_VARS]


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
    dependencies = render_dependencies(read_dependency_results(args.deps)) if args.deps else ""
    sys.stdout.write(
        render_prompt(
            body,
            diff,
            retry=args.retry,
            empty_review=args.retry_empty,
            dependencies=dependencies,
        )
    )
    return 0


def limit_message(run_summary: dict | None, max_cost: str, max_turns: str) -> str:
    """Why a run that stopped at a limit has no result."""
    limit = (run_summary or {}).get("limit") or ("cost" if max_cost else "")
    if limit == "cost":
        what = f"its cost limit (${max_cost})" if max_cost else "its cost limit"
    elif limit == "turns":
        what = f"its turn limit ({max_turns} model calls)" if max_turns else "its turn limit"
    elif limit == "tokens":
        what = "its token limit"
    else:
        what = "a limit"
    return f"The agent stopped at {what} before it returned a result."


def cmd_limit_message(args: argparse.Namespace) -> int:
    stderr_text = ""
    if args.stderr and Path(args.stderr).is_file():
        stderr_text = Path(args.stderr).read_text(errors="replace")
    print(limit_message(read_run_summary(stderr_text), args.max_cost, args.max_turns))
    return 0


def cmd_deps_plan(args: argparse.Namespace) -> int:
    """Print one tab-separated install step per line: ecosystem, project
    directory, image, dependency location, environment (KEY=VALUE, space
    separated), command."""
    try:
        changed = Path(args.changed).read_text().splitlines()
    except OSError:
        changed = []
    for step in deps_plan(Path(args.root), changed):
        env = " ".join(f"{k}={v}" for k, v in step["env"].items())
        print("\t".join([step["ecosystem"], step["dir"] or ".", step["image"], step["deps"], env, step["command"]]))
    return 0


def cmd_empty_review(args: argparse.Namespace) -> int:
    parsed = json.loads(Path(args.verdict).read_text())
    if not isinstance(parsed, dict):
        return 1
    diff_path = Path(args.diff) if args.diff else None
    diff = diff_path.read_text() if diff_path and diff_path.is_file() else ""
    return 0 if reviewed_nothing(parsed, diff) else 1


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
    raw_cap = os.environ.get("CHECKS_MAX_OUTPUT_TOKENS", "").strip()
    max_output_tokens = _positive_int(raw_cap) if raw_cap else None
    if raw_cap and max_output_tokens is None:
        raise SystemExit(
            f"max-output-tokens must be a positive whole number, not '{raw_cap}'."
        )
    sys.stdout.write(
        nextcli_config(provider, model, api_key, base_url, max_output_tokens)
    )
    return 0


def cmd_check_run_body(args: argparse.Namespace) -> int:
    verdict = _read_verdict_file(args.verdict)
    json.dump(
        check_run_body(
            verdict,
            args.conclusion,
            args.details_url or "",
            repo=args.repo or "",
            sha=args.sha or "",
        ),
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


def load_results(directory: Path) -> list[tuple[str, dict]]:
    """Verdict files written by run-check.sh, named check-<name>.verdict.json"""
    results = []
    for path in sorted(directory.rglob("check-*.verdict.json")):
        name = path.name[len("check-") : -len(".verdict.json")]
        try:
            verdict = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(verdict, dict) and verdict.get("verdict"):
            results.append((name, verdict))
    return results


def cmd_render_comment(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    results = load_results(directory) if directory.is_dir() else []
    sys.stdout.write(
        render_comment(results, args.repo or "", args.sha or "", args.max_cost or "")
    )
    return 0


def cmd_annotate(args: argparse.Namespace) -> int:
    path = Path(args.verdict)
    verdict = _read_verdict_file(args.verdict)
    stderr_text = ""
    if args.stderr and Path(args.stderr).is_file():
        stderr_text = Path(args.stderr).read_text(errors="replace")
    annotated = annotate_verdict(
        verdict, read_run_summary(stderr_text), args.limit_exit, args.spent_before
    )
    path.write_text(json.dumps(annotated))
    return 0


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _call_from_trace(event: dict) -> dict:
    """One `llm.request` event of NextCLI's NEXTCLI_TRACE=basic trace.

    The event is {"ts", "dur", "name", "attrs"}. `attrs.usage` is the
    provider's usage as sent; the other attrs are cn's camelCase call fields.
    A call that failed before streaming has `error` (and `status`) but no usage.
    """
    attrs = _dict(event.get("attrs"))
    usage = _dict(attrs.get("usage"))
    prompt_details = _dict(usage.get("prompt_tokens_details"))
    completion_details = _dict(usage.get("completion_tokens_details"))
    return {
        "model": attrs.get("model"),
        "prompt_tokens": _number(usage.get("prompt_tokens")),
        "completion_tokens": _number(usage.get("completion_tokens")),
        "cached_tokens": _number(
            prompt_details.get("cached_tokens", prompt_details.get("cache_read_tokens"))
        ),
        # Providers that hide or encrypt reasoning still count its tokens here
        "reasoning_tokens": _number(completion_details.get("reasoning_tokens")),
        "cost_usd": _number(attrs.get("costUsd")),
        "priced": attrs.get("priced"),
        "duration_ms": _number(event.get("dur")),
        "ttft_ms": _number(attrs.get("ttftMs")),
        "finish_reason": attrs.get("finishReason"),
        "content_chars": _number(attrs.get("contentChars")),
        "reasoning_chars": _number(attrs.get("reasoningChars")),
        "tool_calls": [t for t in attrs.get("toolCalls") or [] if isinstance(t, str)],
        "discarded": bool(attrs.get("discarded")),
        "aborted": bool(attrs.get("aborted")),
        "error": attrs.get("error") if isinstance(attrs.get("error"), str) else None,
        "status": _number(attrs.get("status")),
    }


def read_trace(path: Path) -> list[dict]:
    """The runs in a file of NextCLI traces, oldest first.

    Each cn process writes its own file that starts with a `header` event, and
    run-check.sh concatenates them (a check and its retry), so a header starts
    a run: {"run_id", "version", "started", "calls": [...]}. Lines that are not
    JSON objects, and events other than `header` and `llm.request`, are skipped.
    """
    runs: list[dict] = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        name = event.get("name")
        if name == "header":
            attrs = _dict(event.get("attrs"))
            runs.append(
                {
                    "run_id": attrs.get("runId"),
                    "version": attrs.get("version"),
                    "started": _number(event.get("ts")),
                    "calls": [],
                }
            )
        elif name == "llm.request":
            if not runs:
                runs.append({"run_id": None, "version": None, "started": _number(event.get("ts")), "calls": []})
            runs[-1]["calls"].append(_call_from_trace(event))
    # The files are concatenated in name order (session id), not in time order
    runs.sort(key=lambda run: run["started"] if run["started"] is not None else math.inf)
    return runs


def run_ending(calls: list[dict]) -> str | None:
    """How a run's last model call ended, when that explains a missing answer."""
    kept = [call for call in calls if not call["discarded"]]
    if not kept:
        return None
    last = kept[-1]
    if last["error"]:
        status = f" {last['status']}" if last["status"] is not None else ""
        return f"The last call failed with a provider error{status}."
    if last["aborted"]:
        return "The last call was stopped."
    if last["finish_reason"] == "length":
        return f"The last call was cut off at the output cap ({_n(last['completion_tokens'])} completion tokens)."
    if last["content_chars"] or last["tool_calls"]:
        return None
    thought = [
        f"{_n(value)} reasoning {unit}"
        for value, unit in ((last["reasoning_chars"], "chars"), (last["reasoning_tokens"], "tokens"))
        if value
    ]
    if thought:
        return f"The last call returned no answer and no tool calls after thinking ({', '.join(thought)})."
    return "The last call returned nothing: no answer, no tool calls and no reasoning."


# A retry needs at least this share of the cost limit left to be worth running.
RETRY_MIN_BUDGET_SHARE = 0.1


def retry_budget(run_summary: dict | None, max_cost: float) -> tuple[float, float] | None:
    """(budget left, spent) for a retry of a run with a cost limit, or None when
    the first run's cost is unknown or too little of the limit is left."""
    if not run_summary or max_cost <= 0:
        return None
    usage = run_summary.get("usage")
    cost = usage.get("cost") if isinstance(usage, dict) else None
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost < 0:
        return None
    remaining = max_cost - cost
    if remaining < max_cost * RETRY_MIN_BUDGET_SHARE:
        return None
    return remaining, float(cost)


def cmd_retry_budget(args: argparse.Namespace) -> int:
    stderr_text = ""
    if Path(args.stderr).is_file():
        stderr_text = Path(args.stderr).read_text(errors="replace")
    try:
        max_cost = float(args.max_cost)
    except ValueError:
        return 1
    budget = retry_budget(read_run_summary(stderr_text), max_cost)
    if budget is None:
        return 1
    remaining, spent = budget
    # Round the budget down so the two runs together stay within the limit.
    print(f"{math.floor(remaining * 10000) / 10000:.4f} {spent:.6f}")
    return 0


def _tool_results(session: dict) -> list[tuple[str, str, int]]:
    """(tool, main argument, characters returned) for each tool call."""
    results = []
    for item in session.get("history") or []:
        for state in item.get("toolCallStates") or []:
            function = (state.get("toolCall") or {}).get("function") or {}
            name = function.get("name") or "?"
            try:
                args = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            target = ""
            if isinstance(args, dict):
                for key in ("filepath", "path", "dirpath", "pattern", "command", "url"):
                    if args.get(key):
                        target = str(args[key])
                        break
            size = sum(
                len(str(part.get("content") or ""))
                for part in state.get("output") or []
                if isinstance(part, dict)
            )
            results.append((name, target, size))
    return results


def _n(value: object) -> str:
    return f"{value:,}" if isinstance(value, (int, float)) else "-"


def _seconds(ms: object) -> str:
    return f"{ms / 1000:.1f}" if isinstance(ms, (int, float)) else "-"


def _outcome(call: dict) -> str:
    if call["error"]:
        status = f" {call['status']}" if call["status"] is not None else ""
        outcome = f"error{status}: {_fit(call['error'], 80)}"
        return f"discarded, {outcome}" if call["discarded"] else outcome
    if call["discarded"]:
        return "discarded"
    if call["aborted"]:
        return "aborted"
    return ""


def _totals(calls: list[dict]) -> str:
    kept = [call for call in calls if not call["discarded"]]
    total = lambda key: sum(call[key] or 0 for call in calls)  # noqa: E731
    return (
        f"{len(kept)} model calls ({len(calls) - len(kept)} discarded), "
        f"{_n(total('prompt_tokens'))} prompt, {_n(total('completion_tokens'))} completion, "
        f"{_n(total('cached_tokens'))} cached tokens, ${total('cost_usd'):.2f}, "
        f"{total('duration_ms') / 1000:.0f} s in model calls"
    )


CALL_TABLE_HEADER = (
    "  # | prompt     | completion | cached     | seconds | ttft  | finish     "
    "| answer | reasoning      | tools | outcome"
)


def _call_row(index: int, call: dict) -> str:
    reasoning = _n(call["reasoning_chars"])
    if call["reasoning_tokens"]:
        reasoning += f" ({_n(call['reasoning_tokens'])} tok)"
    tools = ", ".join(call["tool_calls"]) or "-"
    return (
        f"{index:>3} | {_n(call['prompt_tokens']):>10} | {_n(call['completion_tokens']):>10} | "
        f"{_n(call['cached_tokens']):>10} | {_seconds(call['duration_ms']):>7} | "
        f"{_seconds(call['ttft_ms']):>5} | {str(call['finish_reason'] or '-'):<10} | "
        f"{_n(call['content_chars']):>6} | {reasoning:<14} | {tools} | {_outcome(call) or '-'}"
    )


def session_report(runs: list[dict], session: dict | None = None) -> str:
    """A per-call table for each run of one check, how each run ended, and
    what the tools of the last run returned."""
    calls = [call for run in runs for call in run["calls"]]
    lines = [_totals(calls)]
    for number, run in enumerate(runs, 1):
        lines.append("")
        if len(runs) > 1:
            retry = " (retry)" if number > 1 else ""
            lines += [f"Run {number}{retry}: {_totals(run['calls'])}", ""]
        lines.append(CALL_TABLE_HEADER)
        lines += [_call_row(index, call) for index, call in enumerate(run["calls"], 1)]
        ending = run_ending(run["calls"])
        if ending:
            lines.append(ending)
    asked: dict[str, int] = {}
    for call in calls:
        if call["discarded"]:
            continue
        for tool in call["tool_calls"]:
            asked[tool] = asked.get(tool, 0) + 1
    if asked:
        lines += ["", "Tool calls: " + ", ".join(f"{t} x{n}" for t, n in sorted(asked.items(), key=lambda kv: -kv[1]))]
    if session:
        results = _tool_results(session)
        if results:
            by_tool: dict[str, int] = {}
            for name, _target, size in results:
                by_tool[name] = by_tool.get(name, 0) + size
            lines += ["Characters returned: " + ", ".join(f"{t} {_n(c)}" for t, c in sorted(by_tool.items(), key=lambda kv: -kv[1]))]
            largest = sorted(results, key=lambda r: -r[2])[:5]
            lines += ["Largest results: " + "; ".join(f"{name} {target or '-'} {_n(size)}" for name, target, size in largest)]
            seen: dict[tuple[str, str], int] = {}
            for name, target, _size in results:
                if target:
                    seen[(name, target)] = seen.get((name, target), 0) + 1
            repeats = [f"{name} {target} x{count}" for (name, target), count in seen.items() if count > 1]
            if repeats:
                lines += ["Repeated: " + "; ".join(repeats)]
    return "\n".join(lines) + "\n"


def cmd_session_report(args: argparse.Namespace) -> int:
    calls_path = Path(args.calls)
    if not calls_path.is_file():
        print(f"No call log at {calls_path}.", file=sys.stderr)
        return 1
    session = None
    if args.session and Path(args.session).is_file():
        try:
            loaded = json.loads(Path(args.session).read_text(errors="replace"))
            session = loaded if isinstance(loaded, dict) else None
        except json.JSONDecodeError:
            session = None
    sys.stdout.write(session_report(read_trace(calls_path), session))
    return 0


def cmd_redact(args: argparse.Namespace) -> int:
    secrets = secrets_from_env()
    for name in args.files:
        path = Path(name)
        if not path.is_file():
            continue
        text = path.read_text(errors="replace")
        cleaned = redact_text(text, secrets)
        if cleaned != text:
            path.write_text(cleaned)
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
    render.add_argument("--retry-empty", action="store_true")
    render.add_argument("--deps", help="install-deps.sh results: ecosystem, location, status per line")
    render.set_defaults(func=cmd_render_prompt)

    plan = sub.add_parser(
        "deps-plan",
        help="Print the dependency install steps for the projects that hold the changed files.",
    )
    plan.add_argument("--root", required=True)
    plan.add_argument("--changed", required=True)
    plan.set_defaults(func=cmd_deps_plan)

    limit = sub.add_parser(
        "limit-message",
        help="Print why a run that stopped at a limit has no result.",
    )
    limit.add_argument("--stderr")
    limit.add_argument("--max-cost", default=os.environ.get("CHECKS_MAX_COST", ""))
    limit.add_argument("--max-turns", default=os.environ.get("CHECKS_MAX_TURNS", ""))
    limit.set_defaults(func=cmd_limit_message)

    empty = sub.add_parser("empty-review")
    empty.add_argument("--verdict", required=True)
    empty.add_argument("--diff", required=True)
    empty.set_defaults(func=cmd_empty_review)

    parse = sub.add_parser("parse-verdict")
    parse.set_defaults(func=cmd_parse_verdict)

    opencode = sub.add_parser("opencode-config")
    opencode.set_defaults(func=cmd_opencode_config)

    nextcli = sub.add_parser("nextcli-config")
    nextcli.set_defaults(func=cmd_nextcli_config)

    check_run = sub.add_parser("check-run-body")
    check_run.add_argument("--conclusion", required=True)
    check_run.add_argument("--details-url", default="")
    check_run.add_argument("--verdict")
    check_run.add_argument("--repo", default="")
    check_run.add_argument("--sha", default="")
    check_run.set_defaults(func=cmd_check_run_body)

    comment = sub.add_parser(
        "render-comment",
        help="Render the pull request comment for every verdict file in a directory.",
    )
    comment.add_argument("--dir", required=True)
    comment.add_argument("--repo", default="")
    comment.add_argument("--sha", default="")
    comment.add_argument("--max-cost", default="")
    comment.set_defaults(func=cmd_render_comment)

    annotate = sub.add_parser(
        "annotate",
        help="Add the agent's usage to a verdict file, and mark runs ended by a limit as partial.",
    )
    annotate.add_argument("--verdict", required=True)
    annotate.add_argument("--stderr")
    annotate.add_argument("--limit-exit", action="store_true")
    annotate.add_argument("--spent-before", type=float, default=0.0)
    annotate.set_defaults(func=cmd_annotate)

    budget = sub.add_parser(
        "retry-budget",
        help="Print the cost budget left for a retry and what the first run spent, or exit 1 if a retry should not run.",
    )
    budget.add_argument("--stderr", required=True)
    budget.add_argument("--max-cost", required=True)
    budget.set_defaults(func=cmd_retry_budget)

    report = sub.add_parser(
        "session-report",
        help="Print a check's model calls from NextCLI's trace, run by run, and what its tools returned.",
    )
    report.add_argument("--calls", required=True)
    report.add_argument("--session")
    report.set_defaults(func=cmd_session_report)

    redact = sub.add_parser(
        "redact",
        help="Replace secret values from the environment in the given files, in place.",
    )
    redact.add_argument("files", nargs="*")
    redact.set_defaults(func=cmd_redact)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
