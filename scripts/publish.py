"""One command to publish: release check, tests, commit, push.

    python scripts/publish.py "commit message"            check + test + commit + push
    python scripts/publish.py "msg" --no-push             stop after the commit
    python scripts/publish.py --setup                     only enable the git hooks

Never commits private paths (they are git-ignored; the pre-push hook double-checks).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*cmd: str) -> None:
    print("+", " ".join(cmd))
    if subprocess.run(cmd, cwd=ROOT).returncode:
        sys.exit(f"failed: {' '.join(cmd)}")


def main(argv: list[str]) -> None:
    run("git", "config", "core.hooksPath", ".githooks")
    if "--setup" in argv:
        return
    msgs = [a for a in argv if not a.startswith("--")]
    if not msgs:
        sys.exit(__doc__)
    run(sys.executable, "scripts/release_check.py")
    run(sys.executable, "-m", "pytest", "-q")
    run("git", "add", "-A")
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        print("nothing to commit")
    else:
        run("git", "commit", "-m", msgs[0])
    if "--no-push" not in argv:
        run("git", "push", "-u", "origin", "main")


if __name__ == "__main__":
    main(sys.argv[1:])
