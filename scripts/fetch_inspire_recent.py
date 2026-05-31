#!/usr/bin/env python3
"""Fetch recent INSPIRE records for profile pages.

The script scans English profile pages under people/, resolves each person to
an INSPIRE BAI identifier, then fetches papers since a cutoff date. It writes a
machine-readable JSON file and a compact Markdown review file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
import time
import http.client
import urllib.parse
import urllib.request
from pathlib import Path


API = "https://inspirehep.net/api"
DEFAULT_FIELDS = (
    "titles,authors,arxiv_eprints,publication_info,preprint_date,"
    "earliest_date,citation_count,control_number"
)


BAI_OVERRIDES = {
    "Anran Jin": "A.Jin.4",
    "Babak Haghighat": "B.Haghighat.1",
    "Bartek Czech": "Bartlomiej.Czech.1",
    "Benjamin Zhou": "B.T.Zhou.1",
    "Chi-Ming Chang": "C.M.Chang.1",
    "Davide Bason": "D.Bason.1",
    "Junya Yagi": "J.Yagi.1",
    "Ling-Yan Hung": "L.Y.Hung.1",
    "Pengxiang Hao": "P.X.Hao.1",
    "Qingrui Wang": "Q.R.Wang.1",
    "Robert McRae": "R.McRae.1",
    "Sinong Liu": "Si.Nong.Liu.1",
    "Si Li": "Si.Li.1",
    "Takumi Otani": "T.Otani.1",
    "Wei Song": "Wei.Song.1",
    "Wenbin Yan": "W.B.Yan.1",
    "Youssra Boujakhrout": "Y.Boujakhrout.1",
    "Ning Su": "Ning.Su.1",
}


def request_json(url: str, retries: int = 3) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "ymsc-profile-audit/1.0"})
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, http.client.IncompleteRead) as error:
            last_error = error
            if attempt + 1 == retries:
                break
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Could not fetch {url}: {last_error}") from last_error


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def extract_people(people_dir: Path) -> list[dict]:
    people = []
    for path in sorted(people_dir.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        heading = re.search(r"<h2>(.*?)</h2>", text, re.S)
        if not heading:
            continue
        display_name = clean_text(heading.group(1))
        inspire_name = re.sub(r"\s*\([^)]*\)\s*", "", display_name).strip()
        summary = ""
        summary_match = re.search(
            r"<h3>Research Summary</h3>\s*<p>(.*?)</p>", text, re.S
        )
        if summary_match:
            summary = clean_text(summary_match.group(1))
        people.append(
            {
                "file": str(path),
                "display_name": display_name,
                "inspire_name": inspire_name,
                "current_summary": summary,
            }
        )
    return people


def resolve_bai(name: str) -> tuple[str | None, list[dict]]:
    if name in BAI_OVERRIDES:
        return BAI_OVERRIDES[name], []
    query = urllib.parse.quote(name)
    url = f"{API}/authors?q={query}&size=10"
    data = request_json(url)
    candidates = data.get("hits", {}).get("hits", [])
    simplified = []
    for hit in candidates:
        meta = hit.get("metadata", {})
        ids = meta.get("ids", [])
        bai = next((item["value"] for item in ids if item.get("schema") == "INSPIRE BAI"), None)
        author_name = meta.get("name", {}).get("preferred_name") or meta.get("name", {}).get("value")
        categories = meta.get("arxiv_categories", [])
        positions = [
            position.get("institution")
            for position in meta.get("positions", [])
            if position.get("institution")
        ]
        simplified.append(
            {
                "name": author_name,
                "bai": bai,
                "categories": categories,
                "positions": positions[:4],
            }
        )
    normalized = name.lower().replace("-", " ")
    for candidate in simplified:
        candidate_name = (candidate.get("name") or "").lower().replace("-", " ")
        if candidate.get("bai") and normalized == candidate_name:
            return candidate["bai"], simplified
    for candidate in simplified:
        candidate_name = (candidate.get("name") or "").lower().replace("-", " ")
        if candidate.get("bai") and normalized in candidate_name:
            return candidate["bai"], simplified
    return None, simplified


def fetch_literature(bai: str, since: str, size: int, include_abstracts: bool) -> dict:
    query = urllib.parse.quote(f"a {bai} and date > {since}")
    fields_value = DEFAULT_FIELDS + (",abstracts" if include_abstracts else "")
    fields = urllib.parse.quote(fields_value)
    url = (
        f"{API}/literature?q={query}&sort=mostrecent&size={size}"
        f"&fields={fields}"
    )
    return request_json(url)


def record_to_item(hit: dict) -> dict:
    meta = hit.get("metadata", {})
    arxiv = meta.get("arxiv_eprints", [{}])[0].get("value", "")
    categories = meta.get("arxiv_eprints", [{}])[0].get("categories", [])
    publication = meta.get("publication_info", [{}])[0]
    journal = " ".join(
        str(publication.get(key, ""))
        for key in ("journal_title", "journal_volume", "artid", "page_start")
        if publication.get(key)
    )
    authors = [author.get("full_name") for author in meta.get("authors", []) if author.get("full_name")]
    abstract = ""
    if meta.get("abstracts"):
        abstract = clean_text(meta["abstracts"][0].get("value"))
    return {
        "title": clean_text(meta.get("titles", [{}])[0].get("title", "")),
        "date": meta.get("preprint_date") or meta.get("earliest_date") or "",
        "arxiv": arxiv,
        "categories": categories,
        "journal": journal,
        "citation_count": meta.get("citation_count", 0),
        "authors": authors[:8],
        "control_number": meta.get("control_number"),
        "abstract": abstract,
    }


def is_recent(paper: dict, since: str) -> bool:
    date = paper.get("date", "")
    if not date:
        return True
    if len(date) == 4:
        date = f"{date}-01-01"
    if len(date) == 7:
        date = f"{date}-01"
    return date >= since


def write_markdown(records: list[dict], output: Path) -> None:
    lines = [
        "# Recent INSPIRE Papers for Profile Summary Review",
        "",
        f"Generated on {dt.date.today().isoformat()}.",
        "",
    ]
    for person in records:
        lines.append(f"## {person['display_name']}")
        if person.get("bai"):
            lines.append(f"- INSPIRE BAI: `{person['bai']}`")
        else:
            lines.append("- INSPIRE BAI: not resolved")
        lines.append(f"- Recent INSPIRE records: {len(person.get('papers', []))}")
        if person.get("current_summary"):
            lines.append(f"- Current summary: {person['current_summary']}")
        for paper in person.get("papers", [])[:12]:
            citation = f", citations {paper['citation_count']}" if paper.get("citation_count") else ""
            arxiv = f", arXiv:{paper['arxiv']}" if paper.get("arxiv") else ""
            lines.append(f"  - {paper['date']}: {paper['title']}{arxiv}{citation}")
        if person.get("candidates"):
            lines.append("- Author candidates:")
            for candidate in person["candidates"][:4]:
                lines.append(
                    f"  - {candidate.get('name')} | {candidate.get('bai')} | "
                    f"{', '.join(candidate.get('categories') or [])}"
                )
        lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--people-dir", default="people", type=Path)
    parser.add_argument("--since", default="2021-05-31")
    parser.add_argument("--size", default=80, type=int)
    parser.add_argument("--json-output", default="data/inspire-recent.json", type=Path)
    parser.add_argument("--markdown-output", default="data/inspire-recent.md", type=Path)
    parser.add_argument("--sleep", default=0.2, type=float)
    parser.add_argument("--include-abstracts", action="store_true")
    args = parser.parse_args()

    records = []
    for person in extract_people(args.people_dir):
        name = person["inspire_name"]
        print(f"Fetching {name}...", file=sys.stderr)
        bai, candidates = resolve_bai(name)
        person["bai"] = bai
        person["candidates"] = candidates
        papers = []
        if bai:
            data = fetch_literature(bai, args.since, args.size, args.include_abstracts)
            papers = [record_to_item(hit) for hit in data.get("hits", {}).get("hits", [])]
            papers = [paper for paper in papers if is_recent(paper, args.since)]
            person["total"] = data.get("hits", {}).get("total", len(papers))
        person["papers"] = papers
        records.append(person)
        time.sleep(args.sleep)

    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(
            {
                "since": args.since,
                "generated": dt.datetime.now(dt.timezone.utc).isoformat(),
                "people": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_markdown(records, args.markdown_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
