"""Scrape coffee machine manuals from manualslib.com for brands missing on moyo.ua.

ManualsLib uses brand category pages like:
    https://www.manualslib.com/brand/{brand}/coffee-makers.html

Each result page has links to model pages, each model page has a 'Download' button
that leads to the actual PDF.

Usage:
    python3 scripts/scrape_manualslib.py --brand gaggia
    python3 scripts/scrape_manualslib.py --brand melitta --limit 10
    python3 scripts/scrape_manualslib.py --all   # all configured brands
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent
MANUALS_DIR = ROOT / "data" / "manuals"
INDEX_PATH = MANUALS_DIR / "index.json"

BASE = "https://www.manualslib.com"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}

REQUEST_DELAY_S = 2.0

BRAND_SLUGS = {
    "gaggia": "gaggia",
    "melitta": "melitta",
    "smeg": "smeg",
    "tchibo": "tchibo",
    "wmf": "wmf",
    "franke": "franke",
    "kitchenaid": "kitchenaid",
    "breville": "breville",
    "sage": "sage",
    "rancilio": "rancilio",
    "la_marzocco": "la-marzocco",
    "rocket_espresso": "rocket-espresso",
    "nuova_simonelli": "nuova-simonelli",
    "ascaso": "ascaso",
    "aeg": "aeg",
    "zelmer": "zelmer",
    "cecotec": "cecotec",
    "xiaomi": "xiaomi",
    "lavazza": "lavazza",
    "dolce_gusto": "krups",
    "nespresso": "nespresso",
}


def _slugify(text: str) -> str:
    s = text.lower()
    s = re.sub(r"[^\w\s-]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "_", s.strip())
    return s.strip("_")[:80] or "unknown"


def _fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def _list_brand_pages(brand_key: str) -> list[dict]:
    """Find model pages on manualslib brand index."""
    slug = BRAND_SLUGS.get(brand_key, brand_key)
    candidates = [
        f"{BASE}/brand/{slug}/coffee-maker.html",
        f"{BASE}/brand/{slug}/coffee-makers.html",
        f"{BASE}/brand/{slug}/espresso-machine.html",
        f"{BASE}/brand/{slug}/espresso-machines.html",
        f"{BASE}/brand/{slug}/coffee-machine.html",
    ]
    items = []
    seen = set()
    for url in candidates:
        try:
            html = _fetch(url)
        except Exception:
            time.sleep(REQUEST_DELAY_S)
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a.alpha"):
            href = a.get("href", "")
            title = a.get_text(strip=True)
            full_url = urljoin(BASE, href)
            if full_url in seen:
                continue
            seen.add(full_url)
            items.append({
                "brand": brand_key,
                "title": title,
                "slug": _slugify(f"{brand_key}_{title}"),
                "page_url": full_url,
            })
        time.sleep(REQUEST_DELAY_S)
    return items


def _resolve_pdf_url(page_url: str) -> str | None:
    """ManualsLib redirects through a viewer; PDF link is on the model page as
    a 'Download' button referencing /download/<id>/.
    """
    try:
        html = _fetch(page_url)
    except Exception as exc:
        print(f"    [skip page] {exc}")
        return None
    soup = BeautifulSoup(html, "html.parser")
    dl = soup.select_one("a#btn-dl, a.btn-download, a[href*='/download/']")
    if not dl:
        return None
    return urljoin(BASE, dl["href"])


def _download_pdf(item: dict, pdf_url: str, dest_dir: Path) -> Path | None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{item['slug']}.pdf"
    if target.exists() and target.stat().st_size > 0:
        return target
    try:
        r = requests.get(pdf_url, headers=HEADERS, timeout=60, allow_redirects=True)
        r.raise_for_status()
        if r.headers.get("content-type", "").startswith("application/pdf") or r.content[:4] == b"%PDF":
            target.write_bytes(r.content)
            return target
        print(f"    [skip non-pdf] {pdf_url}")
        return None
    except Exception as exc:
        print(f"    [skip dl] {exc}")
        return None


def _load_index() -> list[dict]:
    if INDEX_PATH.exists():
        with open(INDEX_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_index(items: list[dict]):
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--brand", help="Brand key (gaggia, melitta, ...)", default=None)
    parser.add_argument("--all", action="store_true", help="Scrape all configured brands")
    parser.add_argument("--limit", type=int, default=None, help="Max PDFs per brand")
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args()

    if not args.brand and not args.all:
        parser.error("--brand or --all required")

    brands = list(BRAND_SLUGS.keys()) if args.all else [args.brand]
    all_items = []
    for brand in brands:
        print(f"\n=== {brand} ===")
        items = _list_brand_pages(brand)
        print(f"  {len(items)} model pages")
        all_items.extend(items)

    if args.list_only:
        for it in all_items:
            print(f"  {it['brand']:12} | {it['title']}")
        return

    existing = {i["slug"]: i for i in _load_index()}
    by_brand_count: dict[str, int] = {}
    downloaded = 0

    for item in all_items:
        if args.limit and by_brand_count.get(item["brand"], 0) >= args.limit:
            continue
        pdf_url = _resolve_pdf_url(item["page_url"])
        time.sleep(REQUEST_DELAY_S)
        if not pdf_url:
            continue
        path = _download_pdf(item, pdf_url, MANUALS_DIR / item["brand"])
        time.sleep(REQUEST_DELAY_S)
        if path:
            item["local_path"] = str(path.relative_to(ROOT))
            item["downloaded_at"] = int(time.time())
            item["source"] = "manualslib"
            existing[item["slug"]] = item
            by_brand_count[item["brand"]] = by_brand_count.get(item["brand"], 0) + 1
            downloaded += 1
            print(f"  ✓ [{downloaded}] {item['brand']} | {item['title']}")

    _save_index(list(existing.values()))
    print(f"\nDone. Downloaded {downloaded} new PDFs.")


if __name__ == "__main__":
    main()
