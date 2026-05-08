r"""Extract structured sections from a coffee machine PDF manual.

Strategy:
1. Try pdfplumber for text extraction (best for text-based PDFs).
2. Fallback to pypdf if pdfplumber fails.
3. Concatenate all pages, detect Ukrainian/English language by char ratio.
4. Slice by section headers (Помилки, Очищення, Troubleshooting, etc.).
5. Within each section, regex-extract:
   - Error code blocks (E\d{2}: description...)
   - Bulleted/numbered FAQ pairs

Output:
    {
      "brand": str,
      "model": str,
      "title": str,
      "language": "uk" | "en" | "mixed",
      "sections": [
        {"heading": str, "text": str, "page_start": int, "page_end": int}
      ],
      "error_codes": [{"code": "E03", "text": "..."}],
      "raw_text": str  (truncated)
    }

Usage:
    python3 scripts/parse_manual.py data/manuals/delonghi/some.pdf
    python3 scripts/parse_manual.py --all   # parses every PDF in data/manuals/
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
MANUALS_DIR = ROOT / "data" / "manuals"
INDEX_PATH = MANUALS_DIR / "index.json"
PARSED_DIR = MANUALS_DIR / "_parsed"


SECTION_HEADERS = [
    (r"(помилк|повідомлен(ь|ня)\s+помилок|коди\s+помилок)", "errors_uk"),
    (r"(очищен|чищен|деkальцинац|обслуговуван|догляд)", "cleaning_uk"),
    (r"(troubleshoot|troubles|error\s+(codes|messages)|problems)", "troubleshooting_en"),
    (r"(cleaning|maintenance|descal)", "cleaning_en"),
    (r"(приготуван\s+кави|brewing|making\s+coffee)", "brewing"),
    (r"(меню|налаштуван|settings|menu)", "settings"),
    (r"(faq|часті\s+питан|questions)", "faq"),
    (r"(техніч(н)?і\s+характ|technical\s+specifi)", "specs"),
]

ERROR_CODE_RE = re.compile(
    r"\b(E\s?\d{1,3}|ER\s?\d{1,3}|ERR\s?\d{1,3})\b[\s:.\-—]+(.{30,500}?)(?=\b(?:E\s?\d{1,3}|ER\s?\d{1,3}|ERR\s?\d{1,3})\b|\Z)",
    re.DOTALL | re.IGNORECASE,
)

ERROR_CONTEXT_HINTS = re.compile(
    r"(помилк|ошибк|error|пробле|alarm|alert|natiсніть|виправ|service|hibero|відкр|дисплей|повід|виявл|неправил|виконан|reset|перезапу|вимкн|зверн|turn\s+off|press\s+the)",
    re.IGNORECASE,
)


def _extract_pdfplumber(path: Path, max_pages: int = 80) -> list[str] | None:
    try:
        import pdfplumber
    except ImportError:
        return None
    try:
        with pdfplumber.open(path) as pdf:
            pages = pdf.pages[:max_pages]
            return [p.extract_text() or "" for p in pages]
    except Exception:
        return None


def _extract_pypdf(path: Path, max_pages: int = 80) -> list[str] | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        reader = PdfReader(str(path))
        out = []
        for i, p in enumerate(reader.pages):
            if i >= max_pages:
                break
            try:
                out.append(p.extract_text() or "")
            except Exception:
                out.append("")
        return out
    except Exception:
        return None


def _detect_language(text: str) -> str:
    if not text:
        return "unknown"
    cyr = sum(1 for c in text if "Ѐ" <= c <= "ӿ")
    lat = sum(1 for c in text if "a" <= c.lower() <= "z")
    total = cyr + lat
    if total < 50:
        return "unknown"
    cyr_ratio = cyr / total
    if cyr_ratio > 0.5:
        return "uk"
    if cyr_ratio < 0.1:
        return "en"
    return "mixed"


def _detect_sections(pages: list[str]) -> list[dict]:
    sections: list[dict] = []
    full = "\n\n".join(pages)
    cuts: list[tuple[int, str, str]] = []
    for pattern, label in SECTION_HEADERS:
        for m in re.finditer(pattern, full, re.IGNORECASE):
            cuts.append((m.start(), label, m.group(0)))
    if not cuts:
        return [{"heading": "full", "text": full[:8000], "label": "full"}]
    cuts.sort()
    for i, (start, label, heading) in enumerate(cuts):
        end = cuts[i + 1][0] if i + 1 < len(cuts) else len(full)
        body = full[start:end].strip()
        if len(body) < 60:
            continue
        sections.append({"heading": heading, "label": label, "text": body[:6000]})
    return sections


def _extract_error_codes(text: str) -> list[dict]:
    out = []
    seen = set()
    for m in ERROR_CODE_RE.finditer(text):
        code = re.sub(r"\s+", "", m.group(1)).upper()
        code = re.sub(r"^ERR?", "E", code)
        if not re.match(r"^E\d{1,3}$", code):
            continue
        body = m.group(2).strip()
        body = re.sub(r"\s+", " ", body)
        if not ERROR_CONTEXT_HINTS.search(body):
            continue
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "text": body[:600]})
    return out


def parse_pdf(path: Path, brand: str = "", model: str = "", title: str = "") -> dict:
    # pypdf is ~10x faster than pdfplumber; only fall back if pypdf gives nothing.
    pages = _extract_pypdf(path) or _extract_pdfplumber(path) or []
    if not pages or sum(len(p) for p in pages) < 200:
        pages = _extract_pdfplumber(path) or pages
    if not pages:
        return {
            "brand": brand,
            "model": model,
            "title": title,
            "language": "unknown",
            "sections": [],
            "error_codes": [],
            "raw_text": "",
            "error": "no_text_extractable",
        }
    full = "\n\n".join(pages)
    return {
        "brand": brand,
        "model": model,
        "title": title,
        "language": _detect_language(full),
        "page_count": len(pages),
        "sections": _detect_sections(pages),
        "error_codes": _extract_error_codes(full),
        "raw_text": full[:20000],
    }


def _index_lookup(local_path: str) -> dict:
    if not INDEX_PATH.exists():
        return {}
    with open(INDEX_PATH, encoding="utf-8") as f:
        idx = json.load(f)
    for it in idx:
        if it.get("local_path") == local_path:
            return it
    return {}


def _process_one(pdf_path: Path) -> dict:
    rel = str(pdf_path.relative_to(ROOT))
    meta = _index_lookup(rel)
    brand = meta.get("brand", pdf_path.parent.name)
    title = meta.get("title", pdf_path.stem)
    slug = meta.get("slug", pdf_path.stem)
    parsed = parse_pdf(pdf_path, brand=brand, model=slug, title=title)
    out_path = PARSED_DIR / f"{brand}_{slug}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)
    return {"input": rel, "output": str(out_path.relative_to(ROOT)), **parsed}


def _process_path_str(path_str: str) -> dict:
    return _process_one(Path(path_str))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", help="PDF path (omit if --all)")
    parser.add_argument("--all", action="store_true", help="Parse every PDF in data/manuals/")
    parser.add_argument("--summary", action="store_true", help="Print summary table")
    parser.add_argument("--workers", type=int, default=6, help="Parallel workers")
    parser.add_argument("--skip-existing", action="store_true", help="Skip PDFs already parsed")
    args = parser.parse_args()

    if args.all:
        pdfs = sorted(MANUALS_DIR.rglob("*.pdf"))
        if args.skip_existing:
            pdfs = [p for p in pdfs if not (PARSED_DIR / f"{p.parent.name}_{p.stem}.json").exists()]
        print(f"Parsing {len(pdfs)} PDFs with {args.workers} workers...")
        summary = []
        from concurrent.futures import ProcessPoolExecutor, as_completed
        path_strs = [str(p) for p in pdfs]
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(_process_path_str, s): s for s in path_strs}
            done = 0
            for fut in as_completed(futures):
                done += 1
                try:
                    out = fut.result()
                except Exception as exc:
                    print(f"  [{done}/{len(pdfs)}] FAILED: {exc}")
                    continue
                summary.append({
                    "brand": out.get("brand"),
                    "title": out.get("title"),
                    "lang": out.get("language"),
                    "sections": len(out.get("sections", [])),
                    "errors": len(out.get("error_codes", [])),
                    "pages": out.get("page_count", 0),
                })
                print(f"  [{done}/{len(pdfs)}] {out.get('brand'):12} | "
                      f"{out.get('language'):7} | "
                      f"{out.get('page_count', 0):4d}p | "
                      f"{len(out.get('sections', [])):2d}s | "
                      f"{len(out.get('error_codes', [])):2d}e | "
                      f"{out.get('title', '')[:50]}")
        if args.summary:
            print("\n=== Summary ===")
            ok = sum(1 for s in summary if s["lang"] != "unknown")
            print(f"Parsed: {ok}/{len(summary)}, total error codes: {sum(s['errors'] for s in summary)}")
        return

    if not args.path:
        parser.error("PDF path required (or use --all)")

    pdf = Path(args.path)
    if not pdf.is_absolute():
        pdf = ROOT / pdf
    out = _process_one(pdf)
    print(json.dumps(
        {k: v for k, v in out.items() if k != "raw_text"},
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
