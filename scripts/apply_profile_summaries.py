#!/usr/bin/env python3
"""Apply curated bilingual research summaries to profile pages."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path


SUMMARY_RE = {
    "en": re.compile(r"(<h3>Research Summary</h3>\s*<p>)(.*?)(</p>)", re.S),
    "zh": re.compile(r"(<h3>研究简介</h3>\s*<p>)(.*?)(</p>)", re.S),
}


def replace_summary(path: Path, language: str, summary: str) -> None:
    text = path.read_text(encoding="utf-8")
    escaped = html.escape(summary, quote=False)
    pattern = SUMMARY_RE[language]
    new_text, count = pattern.subn(rf"\1{escaped}\3", text, count=1)
    if count != 1:
        raise RuntimeError(f"Could not replace summary in {path}")
    path.write_text(new_text, encoding="utf-8")


def main() -> int:
    data = json.loads(Path("data/profile-summaries.json").read_text(encoding="utf-8"))
    for slug, summaries in data.items():
        replace_summary(Path("people") / f"{slug}.html", "en", summaries["en"])
        replace_summary(Path("zh/people") / f"{slug}.html", "zh", summaries["zh"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
