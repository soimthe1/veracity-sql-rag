"""Retake the README screenshots from a running quick start (demo data only).

    docker compose -f docker-compose.quickstart.yaml up -d --build
    pip install playwright && python -m playwright install chromium
    python scripts/screenshots.py [--url http://localhost:8180] [--channel msedge]

Dark theme, English, public brand. Text matching any regex in
config/local/screenshot-mask.txt (one per line, optional) is replaced before
each capture, so host or model names never reach an image.
Writes docs/images/*.png.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"
MASK_FILE = ROOT / "config" / "local" / "screenshot-mask.txt"

QUESTIONS = [
    ("ask-answer", "Which product category had the most revenue?"),
]
TABS = [("overview", "Overview"), ("entities", "Entities"), ("relationships", "Relationships"),
        ("integrity", "Integrity"), ("glossary", "Glossary")]


def masks() -> list[str]:
    if not MASK_FILE.exists():
        return []
    return [l.strip() for l in MASK_FILE.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def scrub(page: Page, patterns: list[str]) -> None:
    if not patterns:
        return
    page.evaluate("""(pats) => {
        const res = pats.map(p => new RegExp(p, 'gi'));
        const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        for (let n; (n = walk.nextNode());) for (const r of res) n.nodeValue = n.nodeValue.replace(r, '•••');
        for (const el of document.querySelectorAll('input, textarea'))
            for (const r of res) el.value = el.value.replace(r, '•••');
    }""", patterns)


def shot(page: Page, name: str, patterns: list[str]) -> None:
    page.wait_for_timeout(1200)
    scrub(page, patterns)
    page.screenshot(path=str(OUT / f"{name}.png"))
    print("saved", name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8180")
    ap.add_argument("--channel", default=None, help="use an installed browser, e.g. msedge or chrome")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    patterns = masks()

    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel) if args.channel else p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        ctx.add_init_script("localStorage.setItem('ai_catalog_theme','dark');localStorage.setItem('ai_catalog_lang','en');")
        page = ctx.new_page()
        page.goto(args.url, wait_until="networkidle")

        for name, question in QUESTIONS:
            for attempt in range(1, 4):  # the LLM can drop a call; never save a failed answer
                page.goto(args.url, wait_until="networkidle")
                page.locator('button[title="Ask"], button[role="tab"]:has-text("Ask")').first.click()
                box = page.get_by_placeholder("Ask a question in plain language…").first
                box.fill(question)
                box.press("Enter")
                page.wait_for_selector("text=/Tables used/i", timeout=240_000)
                if not page.get_by_text("didn't produce a written answer").count():
                    break
                print(f"attempt {attempt}: no written answer, retrying")
            else:
                raise SystemExit(f"{name}: the LLM gave no answer in 3 attempts; check it and rerun")
            shot(page, name, patterns)

        for name, label in TABS:
            page.locator(f'button[title="{label}"], button[role="tab"]:has-text("{label}")').first.click()
            page.wait_for_load_state("networkidle")
            if name == "relationships":
                page.get_by_text("Whole-schema map").first.click()
                page.wait_for_timeout(4000)  # let the force layout settle
            shot(page, name, patterns)
        browser.close()


if __name__ == "__main__":
    main()
