#!/usr/bin/env python3
"""Discover and collect ROS 2 packages from the LeagueRobotics GitHub org.

Downstream repos opt in to fleet distribution by:
  1. Adding the GitHub topic ``fleet-ros-package`` to the repo, and
  2. Committing a ``fleet.yaml`` manifest at the repo root (see docs/fleet-packages.md).

This script (run on the Ansible control machine) scans the org for that topic, reads
each repo's manifest, clones the repos into a staging dir with vcstool, and writes
``.fleet/packages.lock.yml`` — the authoritative package -> repo -> path -> groups -> sha
list that ``roles/fleet_packages`` consumes to rsync + colcon-build per node.

Prefers the ``gh`` CLI (uses its auth + higher rate limits) and falls back to the
unauthenticated public REST API via urllib. No credentials are required for public repos.

Usage:
  scripts/collect_fleet_packages.py [--org ORG] [--topic TOPIC] [--dry-run] [--offline]

Run from the repo root (the Ansible play sets chdir accordingly).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML ships with Ansible
    sys.exit(
        "error: PyYAML is required (it ships with Ansible). "
        "Install it with: pipx inject ansible pyyaml  ||  apt install python3-yaml"
    )

DEFAULT_ORG = "LeagueRobotics"
DEFAULT_TOPIC = "fleet-ros-package"
FLEET_DIR = Path(".fleet")
STAGING_DIR = FLEET_DIR / "staging"
REPOS_FILE = FLEET_DIR / "fleet.repos"
LOCKFILE = FLEET_DIR / "packages.lock.yml"
MANIFEST_NAME = "fleet.yaml"


# ── output helpers ────────────────────────────────────────────────────────────
def info(msg: str) -> None:
    print(f"[fleet] {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"[fleet] WARNING: {msg}", file=sys.stderr)


def die(msg: str) -> None:
    sys.exit(f"[fleet] ERROR: {msg}")


# ── GitHub access (gh CLI preferred, urllib fallback) ─────────────────────────
_HAVE_GH = shutil.which("gh") is not None


def _gh_api(path: str) -> Any:
    """Call the GitHub REST API via `gh api`, returning parsed JSON."""
    out = subprocess.run(
        ["gh", "api", path], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out)


def _rest_get(url: str) -> Any:
    """Unauthenticated GET against the public REST API."""
    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "ros-deploy"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            die(
                "GitHub API rate limit hit (unauthenticated). Install/auth the `gh` CLI "
                "(`gh auth login`) for higher limits, then re-run."
            )
        raise


def discover_repos(org: str, topic: str) -> list[dict[str, str]]:
    """Return [{full_name, default_branch, clone_url}] for repos carrying the topic."""
    query = f"org:{org}+topic:{topic}"
    repos: list[dict[str, str]] = []
    page = 1
    while True:
        if _HAVE_GH:
            data = _gh_api(
                f"search/repositories?q={query}&per_page=100&page={page}"
            )
        else:
            data = _rest_get(
                f"https://api.github.com/search/repositories?q={query}"
                f"&per_page=100&page={page}"
            )
        items = data.get("items", [])
        for it in items:
            repos.append(
                {
                    "full_name": it["full_name"],
                    "default_branch": it.get("default_branch", "main"),
                    "clone_url": it["clone_url"],
                }
            )
        if len(items) < 100:
            break
        page += 1
    return repos


def fetch_manifest(full_name: str, ref: str) -> dict[str, Any] | None:
    """Fetch + parse fleet.yaml from a repo at the given ref. None if absent."""
    raw_url = f"https://raw.githubusercontent.com/{full_name}/{ref}/{MANIFEST_NAME}"
    req = urllib.request.Request(raw_url, headers={"User-Agent": "ros-deploy"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    try:
        return yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        die(f"{full_name}: {MANIFEST_NAME} is not valid YAML: {exc}")


def resolve_sha(full_name: str, ref: str) -> str:
    """Resolve a git ref to its commit SHA for a reproducible lockfile."""
    if _HAVE_GH:
        data = _gh_api(f"repos/{full_name}/commits/{ref}")
    else:
        data = _rest_get(f"https://api.github.com/repos/{full_name}/commits/{ref}")
    return data["sha"]


# ── manifest validation ───────────────────────────────────────────────────────
def validate_manifest(full_name: str, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate a fleet.yaml and return its normalized package entries."""
    if not isinstance(manifest, dict):
        die(f"{full_name}: {MANIFEST_NAME} must be a mapping")
    pkgs = manifest.get("packages")
    if not isinstance(pkgs, list) or not pkgs:
        die(f"{full_name}: {MANIFEST_NAME} must declare a non-empty `packages` list")
    normalized = []
    for i, pkg in enumerate(pkgs):
        if not isinstance(pkg, dict) or "name" not in pkg:
            die(f"{full_name}: packages[{i}] must be a mapping with a `name`")
        groups = pkg.get("groups", [])
        if not isinstance(groups, list) or not groups:
            die(
                f"{full_name}: package '{pkg['name']}' must declare a non-empty "
                f"`groups` list (use ['all'] for every ros_nodes host)"
            )
        normalized.append(
            {
                "name": pkg["name"],
                "path": pkg.get("path", "."),
                "groups": groups,
            }
        )
    return normalized


# ── lockfile build ────────────────────────────────────────────────────────────
def build_lock(org: str, topic: str) -> dict[str, Any]:
    repos = discover_repos(org, topic)
    info(f"discovered {len(repos)} repo(s) with topic '{topic}' in org '{org}'")

    repositories: dict[str, Any] = {}
    packages: list[dict[str, Any]] = []

    for repo in sorted(repos, key=lambda r: r["full_name"]):
        full_name = repo["full_name"]
        short = full_name.split("/", 1)[1]
        manifest = fetch_manifest(full_name, repo["default_branch"])
        if manifest is None:
            warn(f"{full_name}: has topic '{topic}' but no {MANIFEST_NAME} — skipped")
            continue
        ref = manifest.get("ref", repo["default_branch"])
        sha = resolve_sha(full_name, ref)
        pkgs = validate_manifest(full_name, manifest)

        # vcstool entry: one clone per repo, keyed by its staging subdir.
        repositories[short] = {
            "type": "git",
            "url": repo["clone_url"],
            "version": sha,
        }
        for pkg in pkgs:
            rel = "" if pkg["path"] in (".", "") else "/" + pkg["path"].strip("/")
            packages.append(
                {
                    "name": pkg["name"],
                    "repo": full_name,
                    "sha": sha,
                    "src": f"{STAGING_DIR.as_posix()}/{short}{rel}",
                    "groups": pkg["groups"],
                }
            )
            info(f"  + {pkg['name']:<24} <- {full_name} groups={pkg['groups']}")

    lock = {
        "generated_by": "scripts/collect_fleet_packages.py",
        "org": org,
        "topic": topic,
        "packages": packages,
    }
    return {"lock": lock, "repositories": repositories}


def write_repos_file(repositories: dict[str, Any]) -> None:
    FLEET_DIR.mkdir(exist_ok=True)
    REPOS_FILE.write_text(yaml.safe_dump({"repositories": repositories}, sort_keys=True))


def write_lockfile(lock: dict[str, Any]) -> None:
    FLEET_DIR.mkdir(exist_ok=True)
    header = (
        "# .fleet/packages.lock.yml\n"
        "# GENERATED by scripts/collect_fleet_packages.py — do not edit by hand.\n"
        "# Committed as the auditable record of what the fleet deploys; consumed by\n"
        "# roles/fleet_packages. Regenerate by running the script (or the site playbook).\n"
    )
    LOCKFILE.write_text(header + yaml.safe_dump(lock, sort_keys=False))
    info(f"wrote {LOCKFILE} ({len(lock['packages'])} package(s))")


def vcs_import() -> None:
    """Clone/update the staging workspace from .fleet/fleet.repos via vcstool."""
    if not shutil.which("vcs"):
        die(
            "vcstool not found on the control machine. Install it with: "
            "pipx install vcstool  ||  apt install python3-vcstool"
        )
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    info("vcs import (clone/update staging workspace)...")
    with REPOS_FILE.open() as fh:
        subprocess.run(
            ["vcs", "import", "--recursive", str(STAGING_DIR)],
            stdin=fh,
            check=True,
        )


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", default=os.environ.get("FLEET_ORG", DEFAULT_ORG))
    parser.add_argument("--topic", default=os.environ.get("FLEET_TOPIC", DEFAULT_TOPIC))
    parser.add_argument(
        "--dry-run", action="store_true", help="discover + write lockfile, do not clone"
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip discovery; reuse existing lockfile + staging (no network)",
    )
    args = parser.parse_args()

    if args.offline:
        if not LOCKFILE.exists():
            die(f"--offline given but {LOCKFILE} does not exist; run online once first")
        info(f"offline: reusing existing {LOCKFILE} and staging workspace")
        return

    built = build_lock(args.org, args.topic)
    write_repos_file(built["repositories"])
    write_lockfile(built["lock"])

    if args.dry_run:
        info("--dry-run: skipped vcs import")
        return

    vcs_import()
    info("done.")


if __name__ == "__main__":
    main()
