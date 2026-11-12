#!/usr/bin/env python3
"""Fill the review page template with a project name and accent colour.

    python3 build_page.py --out video-review/index.html [--project "Acme"] [--accent "#ffd23f"]

The output is the file to publish with the Artifact tool. Re-running with the same
--out and republishing that same path keeps the artifact's URL.
"""
import argparse
import html
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "..", "assets", "review-page.html")
DEFAULT_ACCENT = "#ffd23f"


def ink_for(accent: str) -> str:
    """Dark or light text colour that stays readable on the accent."""
    r, g, b = (int(accent[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return "#16150f" if lum > 0.36 else "#ffffff"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="where to write the page (publish this path)")
    ap.add_argument("--project", default="", help="brand or project name shown on the page; leave empty for none")
    ap.add_argument("--accent", default=DEFAULT_ACCENT, help="marker colour as #rrggbb; must read on a dark background")
    a = ap.parse_args()

    accent = a.accent.strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", accent):
        accent = "#" + "".join(c * 2 for c in accent[1:])
    if not re.fullmatch(r"#[0-9a-f]{6}", accent):
        print(f"--accent must be a hex colour like #ffd23f, got {a.accent!r}", file=sys.stderr)
        return 2

    project = " ".join(a.project.split())
    page = open(TEMPLATE, encoding="utf-8").read()
    page = (page
            .replace("__PROJECT__", html.escape(project) + " " if project else "")
            .replace("__PROJECT_HTML__", html.escape(project))
            .replace("__ACCENT_INK__", ink_for(accent))
            .replace("__ACCENT__", accent))
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
