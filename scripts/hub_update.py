#!/usr/bin/env python3
"""Explicit Hub operator update planner.

This command intentionally starts with a no-side-effect `plan` action. It helps a
Hub operator compare a chosen target ref/profile against local operator-state
without treating GitHub main as an automatic deploy trigger.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

SUPPORTED_PROFILES = {
    "local-dev": {
        "description": "Disposable local development stack.",
        "domains": ["source_checkout", "image_build_or_pull", "service_restart", "smoke"],
    },
    "self-hosted-single-node": {
        "description": "Durable single-node operator deployment.",
        "domains": ["source_checkout", "backup_prompt", "image_build_or_pull", "migrations", "service_restart", "smoke"],
    },
    "cloud-prod": {
        "description": "Cloud production deployment with Terraform/CD gates.",
        "domains": ["source_checkout", "image_build_or_pull", "terraform_backend_check", "terraform_plan", "service_rollout", "smoke"],
    },
}


def run_git(repo_dir: Path, args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def resolve_ref(repo_dir: Path, ref: str) -> str:
    try:
        return run_git(repo_dir, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
    except ValueError as exc:
        raise ValueError(f"could not resolve ref {ref!r}: {exc}") from exc


def load_operator_state(state_path: Path) -> dict[str, Any] | None:
    if not state_path.exists():
        return None
    try:
        payload = json.loads(state_path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"operator state is not valid JSON: {state_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"operator state must be a JSON object: {state_path}")
    return payload


def summarize_current(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if state is None:
        return None
    source = state.get("source") if isinstance(state.get("source"), dict) else {}
    images = state.get("images") if isinstance(state.get("images"), dict) else {}
    return {
        "profile": state.get("profile"),
        "source_ref": source.get("ref"),
        "images": images,
        "last_apply": state.get("last_apply"),
    }


def update_domains(profile: str, current: dict[str, Any] | None, resolved_ref: str) -> list[str]:
    domains = list(SUPPORTED_PROFILES[profile]["domains"])
    if current is None:
        domains.insert(0, "bootstrap_operator_state")
        return domains
    if current.get("source_ref") == resolved_ref:
        return ["no_source_change", "smoke"]
    return domains


def build_plan(*, repo_dir: Path, target_ref: str, profile: str, state_path: Path) -> dict[str, Any]:
    repo_dir = repo_dir.resolve()
    state_path = state_path.resolve()
    if profile not in SUPPORTED_PROFILES:
        supported = ", ".join(sorted(SUPPORTED_PROFILES))
        raise ValueError(f"unknown profile {profile!r}; supported profiles: {supported}")
    if not repo_dir.exists():
        raise ValueError(f"repo dir does not exist: {repo_dir}")
    git_root = Path(run_git(repo_dir, ["rev-parse", "--show-toplevel"])).resolve()
    resolved_ref = resolve_ref(git_root, target_ref)
    state = load_operator_state(state_path)
    current = summarize_current(state)
    domains = update_domains(profile, current, resolved_ref)
    return {
        "action": "plan",
        "profile": profile,
        "profile_description": SUPPORTED_PROFILES[profile]["description"],
        "repo_dir": str(git_root),
        "state_path": str(state_path),
        "current": current,
        "target": {
            "requested_ref": target_ref,
            "resolved_ref": resolved_ref,
        },
        "domains": domains,
        "side_effects": False,
        "apply_requires_confirmation": True,
        "notes": [
            "Plan mode does not checkout refs, write state, run Terraform, restart services, or print secrets.",
            "GitHub main is source of truth, not an automatic deployment trigger for this node.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hub operator update planner")
    sub = parser.add_subparsers(dest="action", required=True)
    plan = sub.add_parser("plan", help="Print a no-side-effect update plan")
    add_common_update_args(plan)
    apply = sub.add_parser("apply", help="Apply an operator update with explicit confirmation")
    add_common_update_args(apply)
    apply.add_argument("--dry-run", action="store_true", help="Print the apply envelope without side effects")
    apply.add_argument("--confirm", action="store_true", help="Required for non-dry-run apply")
    return parser


def add_common_update_args(command: argparse.ArgumentParser) -> None:
    command.add_argument("--repo-dir", default=".", help="Hub repository directory")
    command.add_argument("--ref", required=True, dest="target_ref", help="Target git ref/commit/tag")
    command.add_argument("--profile", required=True, choices=sorted(SUPPORTED_PROFILES), help="Deployment profile")
    command.add_argument("--state", required=True, dest="state_path", help="Operator-state JSON path")


def build_apply_envelope(*, repo_dir: Path, target_ref: str, profile: str, state_path: Path, dry_run: bool) -> dict[str, Any]:
    plan = build_plan(repo_dir=repo_dir, target_ref=target_ref, profile=profile, state_path=state_path)
    return {
        "action": "apply",
        "dry_run": dry_run,
        "side_effects": False if dry_run else "pending-implementation",
        "plan": plan,
        "notes": [
            "Apply execution is intentionally guarded; use --dry-run to inspect the envelope without side effects.",
            "Real apply adapters must run smoke checks before writing operator state.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.action == "plan":
            plan = build_plan(
                repo_dir=Path(args.repo_dir),
                target_ref=args.target_ref,
                profile=args.profile,
                state_path=Path(args.state_path),
            )
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        if args.action == "apply":
            if args.profile == "cloud-prod":
                raise ValueError("cloud-prod apply is disabled until Terraform backend access checks are implemented and passing")
            if not args.dry_run and not args.confirm:
                raise ValueError("apply requires --confirm unless --dry-run is supplied")
            envelope = build_apply_envelope(
                repo_dir=Path(args.repo_dir),
                target_ref=args.target_ref,
                profile=args.profile,
                state_path=Path(args.state_path),
                dry_run=args.dry_run,
            )
            print(json.dumps(envelope, indent=2, sort_keys=True))
            return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unsupported action: {args.action}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
