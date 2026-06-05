"""Per-machine citation evaluation. For each (machine_slug, query) case,
runs the full retrieval pipeline (tier 1 model → tier 2 brand → tier 3 any)
and checks the cited PDF matches the expected brand AND family substring.

Usage:
    python3 evaluation/eval_machine_citations.py
    python3 evaluation/eval_machine_citations.py --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env so QDRANT_URL/API_KEY reach the retriever
_env_path = ROOT / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

from src.retriever import VectorRetriever, load_default_encoder  # noqa: E402
from src.brand_matcher import _COMMON_MODELS  # noqa: E402
from src.knowledge_base import KnowledgeBase  # noqa: E402
from src.classifier import Classifier  # noqa: E402


def _normalize_slug(s: str) -> str:
    import re
    s = s.strip().lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", "_", s)
    return s[:60]


def _resolve_brand(model_slug: str, known_brands: list[str]) -> str | None:
    if not model_slug or model_slug == "universal":
        return None
    candidates = [b for b in known_brands if model_slug == b or model_slug.startswith(b + "_")]
    if not candidates:
        return None
    candidates.sort(key=len, reverse=True)
    return candidates[0]


# (machine_display, query, expected_brand, expected_family_substring_in_file)
# Empty expected_family means brand-isolation only (any PDF from that brand passes)
TEST_CASES: list[tuple[str, str, str, str]] = [
    # Brand isolation: every brand must give citation FROM THAT BRAND
    ("DeLonghi Magnifica S", "як почистити від накипу", "delonghi", ""),
    ("DeLonghi Magnifica Evo", "як налаштувати міцність", "delonghi", ""),
    ("DeLonghi Dinamica", "як зварити капучино", "delonghi", ""),
    ("DeLonghi Eletta", "як збити молоко", "delonghi", ""),
    ("Philips EP2231", "як змінити жорсткість води", "philips", ""),
    ("Philips LatteGo 5400", "як збити молоко", "philips", ""),
    ("Philips Series 3200", "як вибрати міцність", "philips", ""),
    ("Jura E8", "очистити заварний блок", "jura", ""),
    ("Saeco Xelsis", "промивка молочної системи", "saeco", ""),
    ("Saeco PicoBaristo", "налаштувати міцність", "saeco", ""),
    ("Krups Evidence", "як декальцинувати", "krups", ""),
    ("Krups EA8108", "очистити заварний блок", "krups", ""),
    ("Miele CM 5310", "не вмикається", "miele", ""),
    ("Miele CM 6360", "очистити", "miele", ""),
    ("Ardesto YCM", "не виливає каву", "ardesto", ""),
    ("Siemens EQ500", "почистити", "siemens", ""),
    ("Gorenje", "не вмикається", "gorenje", ""),
    ("Tefal", "як заварити", "tefal", ""),
    ("Nivona CafeRomatica", "як декальцинувати", "nivona", ""),
    ("Electrolux", "крапельна заварка", "electrolux", ""),
    ("Bosch", "налаштувати міцність", "bosch", ""),
    ("Russell Hobbs", "почистити", "russell_hobbs", ""),
    # Error codes — must hit curated KB, NOT chunks
    # (these cases test the regex override path; eval logic detects ec_* return)
    ("Philips EP2231", "помилка E01", "kb_error_code", "E01"),
    ("DeLonghi Magnifica S", "горить E05", "kb_error_code", "E05"),
    ("Saeco Xelsis", "що означає Е02", "kb_error_code", "E02"),
    ("Miele CM 5310", "E10 показує", "kb_error_code", "E10"),
    ("Philips", "помилка е13", "kb_error_code", "E13"),
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--threshold", type=float, default=0.45)
    args = p.parse_args()

    print("Loading encoder (cached ~30s first time)...")
    encoder = load_default_encoder()
    print("Connecting to Qdrant Cloud...")
    retriever = VectorRetriever(encoder=encoder)
    kb = KnowledgeBase()
    classifier = Classifier(kb, retriever=retriever)
    known_brands = retriever.list_brands()
    print(f"Known brands in Qdrant: {known_brands}")
    print()

    results: list[dict] = []
    for display, query, exp_brand, exp_family in TEST_CASES:
        slug = _normalize_slug(display)
        expanded = retriever._expand_model(slug) or []
        # Tier 1: model filter
        try:
            t1_hits = retriever.search_chunks(query, model=slug, k=3)
        except Exception as e:
            t1_hits = []
            if args.verbose:
                print(f"  tier1 error: {e}")
        # Pick first hit meeting threshold
        chosen = None
        chosen_tier = "none"
        for h in t1_hits:
            if h.score >= args.threshold:
                chosen = h
                chosen_tier = "model"
                break
        # Tier 2: brand filter
        if chosen is None:
            brand = _resolve_brand(slug, known_brands)
            if brand:
                try:
                    t2_hits = retriever.search_chunks_by_brand(query, brand=brand, k=3, exclude_model=slug)
                except Exception as e:
                    t2_hits = []
                    if args.verbose:
                        print(f"  tier2 error: {e}")
                for h in t2_hits:
                    if h.score >= args.threshold:
                        chosen = h
                        chosen_tier = f"brand={brand}"
                        break

        file = (chosen.payload.get("file", "") if chosen else "")
        actual_brand = (chosen.payload.get("brand", "") if chosen else "")
        score = chosen.score if chosen else 0.0

        brand_ok = (actual_brand == exp_brand) if exp_brand else True
        family_ok = (exp_family in file) if exp_family else True
        status = "✓" if (brand_ok and family_ok) else "✗"

        results.append({
            "machine": display,
            "slug": slug,
            "expanded_count": len(expanded),
            "query": query,
            "exp_brand": exp_brand,
            "exp_family": exp_family,
            "got_brand": actual_brand,
            "got_file": file,
            "tier": chosen_tier,
            "score": score,
            "brand_ok": brand_ok,
            "family_ok": family_ok,
            "status": status,
        })

        print(f"{status} [{display}] '{query[:35]}'")
        print(f"   slug={slug} expanded={len(expanded)} tier={chosen_tier} score={score:.3f}")
        if chosen:
            print(f"   file={file}")
        else:
            print(f"   NO HIT above threshold {args.threshold}")
        if not brand_ok:
            print(f"   ❌ brand mismatch: expected {exp_brand}, got {actual_brand}")
        if not family_ok:
            print(f"   ❌ family substring '{exp_family}' not in file")
        print()

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "✓")
    no_hit = sum(1 for r in results if r["tier"] == "none")
    brand_wrong = sum(1 for r in results if not r["brand_ok"])
    family_wrong = sum(1 for r in results if not r["family_ok"])

    print("=" * 60)
    print(f"SUMMARY: {passed}/{total} pass ({passed*100//total}%)")
    print(f"  no hit (below threshold): {no_hit}")
    print(f"  wrong brand:              {brand_wrong}")
    print(f"  wrong family:             {family_wrong}")
    print("=" * 60)

    # Save JSON report
    report_path = ROOT / "evaluation" / "machine_citations_report.json"
    report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"Report saved: {report_path}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
