"""Scrape coffee machine manuals from moyo.ua.

For each brand listed at https://www.moyo.ua/ua/instructions/kofevarki/{brand}/
- Fetch its instruction page
- Extract (model_name, pdf_url) pairs
- Download PDFs into data/manuals/{brand}/{slug}.pdf
- Save metadata index

Usage:
    python3 scripts/scrape_manuals.py                     # all brands, all models
    python3 scripts/scrape_manuals.py --brand delonghi    # one brand
    python3 scripts/scrape_manuals.py --limit 31          # cap total downloads
    python3 scripts/scrape_manuals.py --list-only         # don't download, just list
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

BASE = "https://www.moyo.ua"
INSTRUCTIONS_ROOT = "/ua/instructions/kofevarki/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "uk,en;q=0.5"}

REQUEST_DELAY_S = 1.2

KNOWN_BRANDS = [
    "philips", "electrolux", "gorenje", "bosch", "beko", "sencor", "siemens",
    "delonghi", "russell_hobbs", "moulinex", "tefal", "braun", "einhell",
    "saeco", "miele", "philco", "krups", "ardesto", "jura", "nivona",
    "catler",
]


def _slugify(text: str) -> str:
    s = text.lower()
    s = re.sub(r"інструкція\s+для\s*", "", s)
    s = re.sub(r"кавомашина|кавоварка|кофеварка|кофемашина|кавомолкою|еспресо|капельна|з\s+кавомолкою", "", s)
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"[^\w\s-]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "_", s.strip())
    return s.strip("_")[:80] or "unknown"


def _fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def _list_brand_models(brand: str) -> list[dict]:
    url = f"{BASE}{INSTRUCTIONS_ROOT}{brand}/"
    print(f"  Fetching {url}")
    html = _fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for a in soup.select("a.instruction_list-section_wrapper_title"):
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if not href.endswith(".pdf"):
            continue
        if not title:
            continue
        items.append({
            "brand": brand,
            "title": title,
            "slug": _slugify(title),
            "pdf_url": urljoin(BASE, href),
        })
    return items


def _download_pdf(item: dict, dest_dir: Path) -> Path | None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{item['slug']}.pdf"
    target = dest_dir / fname
    if target.exists() and target.stat().st_size > 0:
        return target
    try:
        r = requests.get(item["pdf_url"], headers=HEADERS, timeout=60)
        r.raise_for_status()
        target.write_bytes(r.content)
        return target
    except Exception as exc:
        print(f"    [skip] {item['title']}: {exc}")
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
    parser.add_argument("--brand", help="Single brand to scrape", default=None)
    parser.add_argument("--limit", type=int, default=None, help="Max PDFs to download")
    parser.add_argument("--list-only", action="store_true", help="List models, don't download")
    args = parser.parse_args()

    brands = [args.brand] if args.brand else KNOWN_BRANDS
    all_items: list[dict] = []
    for brand in brands:
        print(f"\n=== {brand} ===")
        try:
            items = _list_brand_models(brand)
        except Exception as exc:
            print(f"  failed: {exc}")
            continue
        print(f"  found {len(items)} models")
        for item in items:
            all_items.append(item)
        time.sleep(REQUEST_DELAY_S)

    print(f"\nTotal models found: {len(all_items)}")

    if args.list_only:
        for item in all_items[: args.limit or len(all_items)]:
            print(f"  {item['brand']:12} | {item['title']}")
        return

    print("Downloading PDFs...")
    existing = {i["slug"]: i for i in _load_index()}
    downloaded = []
    count = 0
    for item in all_items:
        if args.limit and count >= args.limit:
            break
        dest_dir = MANUALS_DIR / item["brand"]
        path = _download_pdf(item, dest_dir)
        if path:
            item["local_path"] = str(path.relative_to(ROOT))
            item["downloaded_at"] = int(time.time())
            existing[item["slug"]] = item
            downloaded.append(item)
            count += 1
            print(f"  ✓ [{count}] {item['title']}")
        time.sleep(REQUEST_DELAY_S)

    _save_index(list(existing.values()))
    print(f"\nDone. Downloaded {len(downloaded)} new PDFs.")
    print(f"Index: {INDEX_PATH}")
    print(f"Total in index: {len(existing)}")


if __name__ == "__main__":
    main()
