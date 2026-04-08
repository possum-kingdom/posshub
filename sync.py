#!/usr/bin/env python3
"""
PossHub Sync — Keeps your dens in sync with local repos.

Reads the `posshub-source` file in each bare repo to know where
to fetch updates from.

Usage:
    python3 sync.py           # Sync all repos once
    python3 sync.py --watch   # Sync every 5 minutes
    python3 sync.py --watch --interval 60   # Custom interval in seconds
"""

import subprocess
import sys
import time
from pathlib import Path

REPOS_DIR = Path(__file__).parent / "repos"


def sync_repo(repo_path):
    """Fetch latest changes from the source repo."""
    source_file = repo_path / "posshub-source"
    if not source_file.exists():
        return None, "no source configured"

    source = source_file.read_text().strip()
    if not Path(source).exists():
        return False, f"source missing: {source}"

    try:
        result = subprocess.run(
            ["git", "--git-dir", str(repo_path), "fetch", "--all", "--prune"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            return True, "synced"
        else:
            return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "timeout"


def sync_all():
    """Sync all repos."""
    if not REPOS_DIR.exists():
        print("  No repos directory found.")
        return

    repos = sorted(p for p in REPOS_DIR.iterdir()
                   if p.is_dir() and (p / "HEAD").exists())

    if not repos:
        print("  No dens to sync.")
        return

    for repo in repos:
        name = repo.name[:-4] if repo.name.endswith(".git") else repo.name
        ok, msg = sync_repo(repo)
        if ok is None:
            status = "\033[90mskip\033[0m"
        elif ok:
            status = "\033[32m ok \033[0m"
        else:
            status = "\033[31mfail\033[0m"
        print(f"  [{status}] {name}: {msg}")


def main():
    args = sys.argv[1:]
    interval = 300  # default 5 minutes

    watch = "--watch" in args
    if "--interval" in args:
        idx = args.index("--interval")
        if idx + 1 < len(args):
            interval = int(args[idx + 1])

    print(f"""
\033[1mPossHub Sync\033[0m
\033[90mKeeping your dens fresh.\033[0m
""")

    if watch:
        print(f"  Watching every {interval}s. Press Ctrl+C to stop.\n")
        try:
            while True:
                ts = time.strftime("%H:%M:%S")
                print(f"\033[90m[{ts}]\033[0m Syncing...")
                sync_all()
                print()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n  The opossum rests. Goodbye!")
    else:
        sync_all()


if __name__ == "__main__":
    main()
