"""Convert parsed manual sections into KB entries.

Pipeline:
  data/manuals/_parsed/*.json → KB entries appended to data/knowledge_base.json
                              → optional push to Convex.

Two extraction modes:
  - auto (default): generates entries automatically from error codes only.
  - sections (--with-sections): also wraps each detected section as a KB entry.

Usage:
    python3 scripts/manual_to_kb.py                    # all parsed manuals, error codes only
    python3 scripts/manual_to_kb.py --with-sections    # include section entries
    python3 scripts/manual_to_kb.py --brand delonghi   # one brand
    python3 scripts/manual_to_kb.py --dry-run          # don't write
    python3 scripts/manual_to_kb.py --no-convex        # JSON only, skip Convex
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

PARSED_DIR = ROOT / "data" / "manuals" / "_parsed"
KB_PATH = ROOT / "data" / "knowledge_base.json"

CONVEX_URL = os.getenv("CONVEX_URL")

CATEGORY_HINTS = {
    "errors_uk": "error_code",
    "troubleshooting_en": "error_code",
    "cleaning_uk": "cleaning",
    "cleaning_en": "cleaning",
    "brewing": "brewing",
    "settings": "general",
    "faq": "general",
    "specs": "general",
}

MIN_UK_RATIO = 0.30


def _uk_ratio(text: str) -> float:
    if not text:
        return 0.0
    cyr = sum(1 for c in text if "Ѐ" <= c <= "ӿ")
    lat = sum(1 for c in text if "a" <= c.lower() <= "z")
    total = cyr + lat
    return (cyr / total) if total else 0.0


def _extract_uk_paragraphs(text: str) -> str:
    """Return only paragraphs with high Ukrainian character ratio."""
    paras = re.split(r"\n{2,}", text)
    keep = [p for p in paras if _uk_ratio(p) >= MIN_UK_RATIO]
    return "\n\n".join(keep).strip()


def _slug_for_model(brand: str, raw_slug: str) -> str:
    base = re.sub(r"[^\w]", "_", f"{brand}_{raw_slug}".lower())
    base = re.sub(r"_+", "_", base).strip("_")
    return base[:60] or "universal"


def _next_id_for(prefix: str, taken: set[str]) -> str:
    n = 1
    while True:
        cand = f"{prefix}_{n:03d}"
        if cand not in taken:
            taken.add(cand)
            return cand
        n += 1


def _make_error_entry(parsed: dict, ec: dict, taken: set[str]) -> dict | None:
    brand = parsed.get("brand", "unknown")
    model_slug = _slug_for_model(brand, parsed.get("model", "unknown"))
    code = ec["code"].upper()
    text = ec["text"].strip()
    uk_text = _extract_uk_paragraphs(text)
    answer = uk_text if uk_text else text
    if _uk_ratio(answer) < 0.20 and _uk_ratio(text) < 0.10:
        return None
    eid = _next_id_for(f"kb_{model_slug}_err_{code.lower()}", taken)
    keywords = [
        code, code.lower(), f"помилка {code}", f"error {code}",
        brand, parsed.get("title", "")[:50],
    ]
    keywords = [k for k in keywords if k]
    question = f"Що означає помилка {code} на {parsed.get('title') or brand}?"
    return {
        "id": eid,
        "category": "error_code",
        "keywords": keywords,
        "question": question,
        "answer": answer[:1500],
        "model": model_slug,
    }


def _make_section_entry(parsed: dict, section: dict, taken: set[str]) -> dict | None:
    brand = parsed.get("brand", "unknown")
    model_slug = _slug_for_model(brand, parsed.get("model", "unknown"))
    label = section.get("label", "section")
    category = CATEGORY_HINTS.get(label, "general")
    text = section.get("text", "").strip()
    uk_text = _extract_uk_paragraphs(text)
    if len(uk_text) < 120:
        return None
    eid = _next_id_for(f"kb_{model_slug}_{label}", taken)
    heading = section.get("heading", label).strip()
    title = parsed.get("title") or brand
    question = f"{heading.capitalize()} — {title}"[:140]
    keywords = [label, brand, heading[:40], title[:40]]
    keywords = [k for k in keywords if k]
    return {
        "id": eid,
        "category": category,
        "keywords": keywords,
        "question": question,
        "answer": uk_text[:3000],
        "model": model_slug,
    }


def _load_kb() -> dict:
    with open(KB_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_kb(data: dict):
    with open(KB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _push_convex(entry: dict):
    if not CONVEX_URL:
        return
    from convex import ConvexClient
    client = ConvexClient(CONVEX_URL)
    client.mutation("kb:upsertEntry", {
        "entryId": entry["id"],
        "category": entry["category"],
        "keywords": entry["keywords"],
        "question": entry["question"],
        "answer": entry["answer"],
        "model": entry["model"],
    })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--brand", default=None, help="Limit to one brand")
    parser.add_argument("--with-sections", action="store_true", help="Also generate section entries")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-convex", action="store_true")
    args = parser.parse_args()

    if not PARSED_DIR.exists():
        print(f"No parsed manuals at {PARSED_DIR}. Run parse_manual.py --all first.")
        sys.exit(1)

    parsed_files = sorted(PARSED_DIR.glob("*.json"))
    if args.brand:
        parsed_files = [p for p in parsed_files if p.name.startswith(f"{args.brand}_")]

    if not parsed_files:
        print("No parsed files match.")
        sys.exit(0)

    kb = _load_kb()
    taken: set[str] = {e["id"] for e in kb["entries"]}
    new_models: set[str] = set()
    new_entries: list[dict] = []

    for pf in parsed_files:
        with open(pf, encoding="utf-8") as f:
            parsed = json.load(f)
        if parsed.get("error") == "no_text_extractable":
            continue

        for ec in parsed.get("error_codes", []):
            entry = _make_error_entry(parsed, ec, taken)
            if entry:
                new_entries.append(entry)
                new_models.add(entry["model"])

        if args.with_sections:
            best_per_label: dict[str, dict] = {}
            for section in parsed.get("sections", []):
                entry = _make_section_entry(parsed, section, taken)
                if not entry:
                    continue
                label = section.get("label", "section")
                cur = best_per_label.get(label)
                if cur is None or len(entry["answer"]) > len(cur["answer"]):
                    best_per_label[label] = entry
            for entry in best_per_label.values():
                new_entries.append(entry)
                new_models.add(entry["model"])

    print(f"Parsed {len(parsed_files)} manuals → {len(new_entries)} new KB entries "
          f"across {len(new_models)} models")

    if args.dry_run:
        for e in new_entries[:10]:
            print(f"  {e['id']:60} {e['category']:12} {e['model']}")
        print(f"  ...({len(new_entries)} total)")
        return

    kb["entries"].extend(new_entries)
    _save_kb(kb)
    print(f"✓ Wrote {len(new_entries)} entries to {KB_PATH}")

    if not args.no_convex and CONVEX_URL:
        print("Pushing to Convex...")
        for i, e in enumerate(new_entries, 1):
            _push_convex(e)
            if i % 50 == 0:
                print(f"  ...{i}/{len(new_entries)}")
        print(f"✓ Pushed {len(new_entries)} to Convex")
    else:
        print("Skipped Convex push.")


if __name__ == "__main__":
    main()
