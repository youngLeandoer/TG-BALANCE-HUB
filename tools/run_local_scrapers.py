#!/usr/bin/env python3
"""
Run all local browser scrapers in one command.

This script orchestrates existing Playwright helpers:
- tools/adminvps_local_browser.py
- tools/atlex_local_browser.py
- tools/nic_local_browser.py

By default runs them sequentially (headed browsers don't play well in parallel).
Exit code is non-zero if any scraper fails.
"""

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class ScraperSpec:
    key: str
    script_rel_path: str


SCRAPERS: dict[str, ScraperSpec] = {
    "adminvps": ScraperSpec(key="adminvps", script_rel_path="tools/adminvps_local_browser.py"),
    "atlex": ScraperSpec(key="atlex", script_rel_path="tools/atlex_local_browser.py"),
    "nic": ScraperSpec(key="nic", script_rel_path="tools/nic_local_browser.py"),
}


def _repo_root() -> str:
    # tools/run_local_scrapers.py -> repo root
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _run_one(spec: ScraperSpec, *, python: str, extra_args: list[str]) -> int:
    cmd = [python, os.path.join(_repo_root(), spec.script_rel_path), *extra_args]
    print(f"\n=== Running {spec.key} ===")
    print("Command:", " ".join(cmd))
    proc = subprocess.run(cmd)
    return int(proc.returncode)


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Run all local browser scrapers")
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated list: adminvps,atlex,nic (default: adminvps,atlex,nic)",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter to use (default: current interpreter)",
    )
    parser.add_argument(
        "--",
        dest="pass_through",
        nargs=argparse.REMAINDER,
        help="Extra args to pass to each scraper (rarely needed)",
    )
    args = parser.parse_args()

    selected = []
    if args.only.strip():
        for raw in args.only.split(","):
            key = raw.strip().lower()
            if not key:
                continue
            if key not in SCRAPERS:
                raise SystemExit(f"Unknown scraper '{key}'. Available: {', '.join(SCRAPERS.keys())}")
            selected.append(key)
    else:
        selected = ["adminvps", "atlex", "nic"]

    extra_args = list(args.pass_through or [])
    if extra_args[:1] == ["--"]:
        extra_args = extra_args[1:]

    failures: list[tuple[str, int]] = []
    for key in selected:
        rc = _run_one(SCRAPERS[key], python=args.python, extra_args=extra_args)
        if rc != 0:
            failures.append((key, rc))

    if failures:
        print("\n=== Failures ===")
        for key, rc in failures:
            print(f"- {key}: exit code {rc}")
        return 1

    print("\nAll scrapers completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

