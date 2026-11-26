"""Release check: scan every file git would commit for private details.

Generic leaks: non-local IPv4 addresses and e-mail addresses outside example/noreply domains.
Private words: config/local/release-words.txt (one regex per line, git-ignored), so this
script itself carries no organisation detail.

    python scripts/release_check.py      exit 0 = ready, 1 = not ready
"""
from __future__ import annotations

import ipaddress
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORDS_FILE = ROOT / "config" / "local" / "release-words.txt"
PRIVATE_PATHS = re.compile(r"^(config/local/|branding/brand\.local/|docs/local/|data/)|(^|/)\.env(\.|$)(?!.*example)")
IP_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
OK_EMAIL_DOMAINS = re.compile(r"(^|\.)(example\.(com|org|net)|corp\.local|example\.internal|localhost|anthropic\.com|users\.noreply\.github\.com)$", re.I)
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".woff", ".woff2", ".ttf", ".tar", ".zip"}


def committable_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [f for f in out.split("\0") if f]


def ip_is_leak(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False  # dotted version strings with parts above 255
    if ip.is_loopback or ip.is_unspecified or ip.is_link_local:
        return False
    # RFC 5737 documentation ranges are fine in examples.
    return not any(ip in ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))


def scan(path: str, text: str, words: list[re.Pattern]) -> list[str]:
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in IP_RE.finditer(line):
            if ip_is_leak(m.group(1)):
                hits.append(f"{path}:{n}: IP {m.group(1)}")
        for m in EMAIL_RE.finditer(line):
            if not OK_EMAIL_DOMAINS.search(m.group(1)) and not path.endswith((".lock", "package-lock.json")):
                hits.append(f"{path}:{n}: e-mail {m.group(0)}")
        for w in words:
            if w.search(line):
                hits.append(f"{path}:{n}: word /{w.pattern}/")
    return hits


def main() -> int:
    words = []
    if WORDS_FILE.exists():
        words = [re.compile(l.strip(), re.I) for l in WORDS_FILE.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.lstrip().startswith("#")]
    else:
        print(f"note: {WORDS_FILE.relative_to(ROOT)} missing; checking generic leaks only")

    problems, binaries = [], []
    for f in committable_files():
        if PRIVATE_PATHS.search(f):
            problems.append(f"{f}: private path would be committed")
            continue
        if Path(f).suffix.lower() in BINARY_EXT:
            binaries.append(f)
            continue
        try:
            text = (ROOT / f).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        problems += scan(f, text, words)

    for p in problems:
        print(p)
    if binaries:
        print(f"\n{len(binaries)} image/PDF file(s) not text-scanned; review them by eye:")
        for b in binaries:
            print("  " + b)
    print(f"\n{'NOT READY' if problems else 'Ready'}: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
