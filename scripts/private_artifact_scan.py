#!/usr/bin/env python3
"""Guardrail scanner for keeping the public Hub repo free of local/private state.

Default mode scans staged files for pre-commit use.
Pre-push mode should scan the outbound range, usually origin/main...HEAD.

Examples:
  python3 scripts/private_artifact_scan.py
  python3 scripts/private_artifact_scan.py --range origin/main...HEAD
  python3 scripts/private_artifact_scan.py --all-tracked

Bypass for intentional local operations:
  ALLOW_PRIVATE_ARTIFACTS=1 git commit ...
  ALLOW_PRIVATE_ARTIFACTS=1 git push ...
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATH_DENYLIST: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(^|/)\.hermes(/|$)"), "Hermes runtime/session state is local-only"),
    (re.compile(r"(^|/)\.env($|\.(?!example$).+)"), "real env files are local-only; commit .env.example instead"),
    (re.compile(r"(^|/)\.tmp(-summary)?(/|$)"), "temporary runtime artifacts are local-only"),
    (re.compile(r"(^|/)data/(?!\.gitkeep$)"), "runtime data must stay out of the public repo"),
    (re.compile(r"(^|/).*\.(db|sqlite|sqlite3)(-(wal|shm))?$"), "database files are runtime state"),
    (re.compile(r"(^|/)repos/workspace/(?!\.gitkeep$)"), "workspace repos are local/client/operator-specific"),
    (re.compile(r"(^|/)infra/matrix/appservices/.*\.resolved\.ya?ml$"), "resolved Matrix appservice registrations contain local tokens"),
    (re.compile(r"(^|/)infra/matrix/config/homeserver-local\.ya?ml$"), "local Synapse config is operator-specific"),
    (re.compile(r"(^|/)infra/aws_baseline_80/(terraform\.tfvars|.*\.tfstate.*|last-image-tag\.local)$"), "Terraform/local deploy state must not be public"),
    (re.compile(r"(^|/)rolodex/agents/[^/]+/(auth\.json|auth\.lock|processes\.json|logs/|sessions/|kanban/)"), "agent auth/session/process runtime state is local-only"),
    (re.compile(r"(^|/)rolodex/agents/[^/]+/memories/.*\.lock$"), "agent memory locks are runtime state"),
    (re.compile(r"(^|/)(capture|notes)/.*(transcript|review|dm|x-dm|conversation pull).*", re.I), "private capture/review/DM material belongs in ClaudeHub or local storage, not public Hub"),
]

CONTENT_DENYLIST: list[tuple[re.Pattern[bytes], str]] = [
    (re.compile(rb"/Users/bananawalnut/"), "absolute local user path"),
    (re.compile(rb"(?i)claude-hub"), "private ClaudeHub path/reference"),
    (re.compile(rb"(?i)capture/x-dm-pulls|x-dm-pulls|dm[_ -]?pull"), "raw DM/X pull reference"),
    (re.compile(rb"(?i)(conversation|message|session)[_-]?id[ \t]*[:=][ \t]*['\"]?[A-Za-z0-9_.:-]{16,}"), "possible raw conversation/message/session identifier"),
    (re.compile(rb"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|session[_-]?token|appservice[_-]?token|registration[_-]?token|admin[_-]?token|bot[_-]?token)[ \t]*[:=][ \t]*['\"]?[A-Za-z0-9_./+=:-]{12,}"), "possible secret/token assignment"),
    (re.compile(rb"(?i)(password|passwd|pwd)[ \t]*[:=][ \t]*['\"]?[^'\"\s]{8,}"), "possible password assignment"),
    (re.compile(rb"(?i)postgres(ql)?://[^\s'\"]+:[^\s'\"]+@"), "database URL with embedded password"),
    (re.compile(rb"(?i)mongodb(\+srv)?://[^\s'\"]+:[^\s'\"]+@"), "database URL with embedded password"),
    (re.compile(rb"AKIA[0-9A-Z]{16}"), "possible AWS access key ID"),
    (re.compile(rb"ASIA[0-9A-Z]{16}"), "possible AWS temporary access key ID"),
    (re.compile(rb"sk-[A-Za-z0-9_-]{20,}"), "possible OpenAI-style API key"),
    (re.compile(rb"gh[pousr]_[A-Za-z0-9_]{20,}"), "possible GitHub token"),
]

TEXT_SUFFIXES = {
    ".adoc", ".cfg", ".conf", ".css", ".csv", ".dockerignore", ".env", ".example",
    ".html", ".ini", ".js", ".json", ".jsonl", ".md", ".mjs", ".py", ".rst",
    ".sh", ".tf", ".tfvars", ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml",
}

CONTENT_ALLOWLIST = {
    ".gitignore",
    "scripts/private_artifact_scan.py",
}

BENIGN_CONTENT_MARKERS = (
    b"***",
    b"REPLACE_ME",
    b"<password>",
    b"your-",
    b"example",
    b"dev_password",
    b"{password",
    b"${",
    b"quote_plus",
    b"settings.",
    b"os.environ",
    b"payload.session_id",
)


def git(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=check)


def git_out(args: list[str]) -> str:
    return git(args).stdout


def staged_paths() -> list[str]:
    out = git_out(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])
    return sorted(line for line in out.splitlines() if line)


def tracked_paths() -> list[str]:
    out = git_out(["ls-files"])
    return sorted(line for line in out.splitlines() if line)


def range_paths(spec: str) -> list[str]:
    out = git_out(["diff", "--name-only", "--diff-filter=ACMR", spec])
    return sorted(line for line in out.splitlines() if line)


def is_probably_text(rel: str) -> bool:
    path = Path(rel)
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in {".gitignore", "Dockerfile", "AGENTS.md", "README"}


def read_worktree_bytes(rel: str) -> bytes | None:
    abs_path = ROOT / rel
    if not abs_path.is_file():
        return None
    try:
        return abs_path.read_bytes()
    except OSError:
        return None


def read_head_bytes(rel: str) -> bytes | None:
    proc = git(["show", f"HEAD:{rel}"], check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.encode()


def scan(paths: list[str], *, source: str, content: bool) -> list[str]:
    findings: list[str] = []
    for rel in paths:
        for pattern, reason in PATH_DENYLIST:
            if pattern.search(rel):
                findings.append(f"PATH  {rel}\n      {reason}")
        if not content or rel in CONTENT_ALLOWLIST or not is_probably_text(rel):
            continue
        data = read_head_bytes(rel) if source == "head" else read_worktree_bytes(rel)
        if data is None or b"\0" in data[:4096]:
            continue
        for pattern, reason in CONTENT_DENYLIST:
            match = pattern.search(data)
            if match:
                line_start = data.rfind(b"\n", 0, match.start()) + 1
                line_end = data.find(b"\n", match.end())
                if line_end == -1:
                    line_end = len(data)
                line = data[line_start:line_end]
                if any(marker in line for marker in BENIGN_CONTENT_MARKERS):
                    continue
                line_no = data[: match.start()].count(b"\n") + 1
                findings.append(f"TEXT  {rel}:{line_no}\n      {reason}")
    return findings


def default_push_range() -> str | None:
    upstream = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], check=False)
    if upstream.returncode == 0 and upstream.stdout.strip():
        return f"{upstream.stdout.strip()}...HEAD"
    remote = git(["rev-parse", "--verify", "origin/main"], check=False)
    if remote.returncode == 0:
        return "origin/main...HEAD"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all-tracked", action="store_true", help="scan all tracked files in the worktree")
    group.add_argument("--range", dest="range_spec", help="scan paths changed in a git range, e.g. origin/main...HEAD")
    group.add_argument("--pre-push", action="store_true", help="scan the current branch's upstream...HEAD range")
    parser.add_argument("--paths-only", action="store_true", help="skip content scanning")
    args = parser.parse_args()

    source = "worktree"
    if args.all_tracked:
        paths = tracked_paths()
        scope = f"tracked files: {len(paths)}"
    elif args.range_spec:
        paths = range_paths(args.range_spec)
        source = "head"
        scope = f"range {args.range_spec}: {len(paths)} paths"
    elif args.pre_push:
        spec = default_push_range()
        if not spec:
            print("private_artifact_scan: no upstream/origin/main range found; skipping pre-push scan")
            return 0
        paths = range_paths(spec)
        source = "head"
        scope = f"range {spec}: {len(paths)} paths"
    else:
        paths = staged_paths()
        scope = f"staged files: {len(paths)}"

    if not paths:
        print(f"private_artifact_scan: clean ({scope})")
        return 0

    findings = scan(paths, source=source, content=not args.paths_only)
    if not findings:
        print(f"private_artifact_scan: clean ({scope})")
        return 0

    print("private_artifact_scan: possible private/local artifacts found\n")
    print("\n".join(findings))
    print("\nIf this is intentional, rerun with ALLOW_PRIVATE_ARTIFACTS=1.")

    if os.environ.get("ALLOW_PRIVATE_ARTIFACTS") == "1":
        print("ALLOW_PRIVATE_ARTIFACTS=1 set; allowing operation despite findings.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
