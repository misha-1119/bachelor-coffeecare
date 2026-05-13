"""Ingest PDF manuals from data/manuals/<brand>/*.pdf into Qdrant `kb_chunks`.

- Walks brand subdirectories (skips `_parsed/` and `index.json`).
- Extracts text per page with pdfplumber (fallback: pypdf) and concatenates sentences
  into ~600-token chunks with 100-token overlap, tracking page_start/page_end.
- Derives `brand` from parent directory and `model` slug from filename using the
  same rule as bot._normalize_model_input.
- Idempotent: drops and recreates the `kb_chunks` collection.

Usage:
    python3 scripts/ingest_pdfs.py
    python3 scripts/ingest_pdfs.py --limit 20    # ingest only first 20 PDFs
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.retriever import (  # noqa: E402
    CHUNKS_COLLECTION,
    VectorRetriever,
    load_default_encoder,
)

MANUALS_DIR = ROOT / "data" / "manuals"
SKIP_DIRS = {"_parsed"}

TARGET_TOKENS = 600
OVERLAP_TOKENS = 100


def normalize_model_slug(stem: str) -> str:
    cleaned = stem.strip().lower()
    cleaned = re.sub(r"[^\w\s]", "", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned.strip())
    return cleaned[:60] or "universal"


def extract_pages(pdf_path: Path) -> list[tuple[int, str]]:
    """Return [(page_number, text), ...]. Tries pdfplumber, falls back to pypdf."""
    try:
        import pdfplumber

        pages: list[tuple[int, str]] = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
                pages.append((i, text))
        if any(t for _, t in pages):
            return pages
    except Exception as exc:
        print(f"  [pdfplumber failed: {exc}] falling back to pypdf")
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        return [(i + 1, (p.extract_text() or "")) for i, p in enumerate(reader.pages)]
    except Exception as exc:
        print(f"  [pypdf failed: {exc}]")
        return []


def approx_tokens(text: str) -> int:
    # Cheap heuristic — 1 token ≈ 0.75 words. Good enough for chunking guard.
    return max(1, int(len(text.split()) / 0.75))


@dataclass
class Chunk:
    text: str
    page_start: int
    page_end: int


def chunk_pages(pages: list[tuple[int, str]]) -> list[Chunk]:
    """Greedy sentence-pack chunks to ~TARGET_TOKENS with OVERLAP_TOKENS overlap."""
    sentences: list[tuple[int, str]] = []
    for page_no, text in pages:
        if not text.strip():
            continue
        for raw in re.split(r"(?<=[\.\!\?])\s+|\n{2,}", text):
            s = raw.strip()
            if s:
                sentences.append((page_no, s))
    if not sentences:
        return []

    chunks: list[Chunk] = []
    buf: list[tuple[int, str]] = []
    buf_tokens = 0
    for page_no, sent in sentences:
        st = approx_tokens(sent)
        if buf_tokens + st > TARGET_TOKENS and buf:
            joined = " ".join(s for _, s in buf).strip()
            chunks.append(Chunk(text=joined, page_start=buf[0][0], page_end=buf[-1][0]))
            overlap: list[tuple[int, str]] = []
            o_tok = 0
            for ps in reversed(buf):
                o_tok += approx_tokens(ps[1])
                overlap.append(ps)
                if o_tok >= OVERLAP_TOKENS:
                    break
            buf = list(reversed(overlap))
            buf_tokens = sum(approx_tokens(s) for _, s in buf)
        buf.append((page_no, sent))
        buf_tokens += st
    if buf:
        joined = " ".join(s for _, s in buf).strip()
        chunks.append(Chunk(text=joined, page_start=buf[0][0], page_end=buf[-1][0]))
    return chunks


def discover_pdfs(limit: int | None) -> list[Path]:
    paths: list[Path] = []
    for brand_dir in sorted(MANUALS_DIR.iterdir()):
        if not brand_dir.is_dir() or brand_dir.name in SKIP_DIRS:
            continue
        for pdf in sorted(brand_dir.glob("*.pdf")):
            paths.append(pdf)
    if limit:
        paths = paths[:limit]
    return paths


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="ingest only first N PDFs (debug)")
    ap.add_argument("--min-chunk-chars", type=int, default=40)
    args = ap.parse_args()

    pdfs = discover_pdfs(args.limit)
    if not pdfs:
        print(f"[ingest_pdfs] no PDFs under {MANUALS_DIR}")
        return 1
    print(f"[ingest_pdfs] {len(pdfs)} PDFs to process")

    print("[ingest_pdfs] loading encoder...")
    encoder = load_default_encoder()
    retriever = VectorRetriever(encoder)
    print(f"[ingest_pdfs] db_path={retriever.db_path} dim={retriever.vector_size}")
    retriever.reset_collection(CHUNKS_COLLECTION)

    total_chunks = 0
    t0 = time.time()
    for i, pdf in enumerate(pdfs, start=1):
        brand = pdf.parent.name
        model_slug = normalize_model_slug(pdf.stem)
        rel_file = str(pdf.relative_to(ROOT))
        print(f"[{i}/{len(pdfs)}] {rel_file}")
        pages = extract_pages(pdf)
        if not pages:
            print("  no text extracted, skipping")
            continue
        chunks = chunk_pages(pages)
        items = []
        for j, ch in enumerate(chunks):
            if len(ch.text) < args.min_chunk_chars:
                continue
            items.append(
                {
                    "chunk_id": f"{brand}/{pdf.stem}#{j:04d}",
                    "brand": brand,
                    "model": model_slug,
                    "file": rel_file,
                    "page_start": ch.page_start,
                    "page_end": ch.page_end,
                    "section": "",
                    "text": ch.text,
                }
            )
        if not items:
            continue
        retriever.upsert_chunks(items)
        total_chunks += len(items)
        print(f"  {len(items)} chunks (cumulative {total_chunks})")

    dt = time.time() - t0
    print(f"[ingest_pdfs] done: {total_chunks} chunks in {dt:.1f}s")
    print(f"[ingest_pdfs] collection size: {retriever.count(CHUNKS_COLLECTION)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
