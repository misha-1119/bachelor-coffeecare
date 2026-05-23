"""Ingest PDF manuals from data/manuals/<brand>/*.pdf into Qdrant `kb_chunks`.

- Walks brand subdirectories (skips `_parsed/` and `index.json`).
- Table pages: pdfplumber table extraction → one Chunk per data row (fixes
  column-interleaving that corrupts troubleshooting tables).
- Prose pages: sentence-packed ~300-token chunks with 50-token overlap.
- Derives `brand` from parent directory and `model` slug from filename.
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
from dataclasses import dataclass, field
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

TARGET_TOKENS = 300   # was 600 — smaller = tighter embeddings
OVERLAP_TOKENS = 50   # was 100

# Minimum columns / rows for pdfplumber table to be treated as structured data
MIN_TABLE_COLS = 2
MIN_TABLE_ROWS = 2


def normalize_model_slug(stem: str) -> str:
    cleaned = stem.strip().lower()
    cleaned = re.sub(r"[^\w\s]", "", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned.strip())
    return cleaned[:60] or "universal"


@dataclass
class PageData:
    page_no: int
    text: str
    tables: list[list[list[str | None]]] = field(default_factory=list)


def extract_pages(pdf_path: Path) -> list[PageData]:
    """Return PageData per page. Tries pdfplumber (with table extraction), falls back to pypdf."""
    try:
        import pdfplumber

        pages: list[PageData] = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
                try:
                    raw_tables = page.extract_tables() or []
                    # Keep only tables with enough structure
                    tables = [
                        t for t in raw_tables
                        if len(t) >= MIN_TABLE_ROWS
                        and t
                        and len(t[0]) >= MIN_TABLE_COLS
                    ]
                except Exception:
                    tables = []
                pages.append(PageData(page_no=i, text=text, tables=tables))
        if any(p.text or p.tables for p in pages):
            return pages
    except Exception as exc:
        print(f"  [pdfplumber failed: {exc}] falling back to pypdf")
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        return [
            PageData(page_no=i + 1, text=(p.extract_text() or ""))
            for i, p in enumerate(reader.pages)
        ]
    except Exception as exc:
        print(f"  [pypdf failed: {exc}]")
        return []


def approx_tokens(text: str) -> int:
    return max(1, int(len(text.split()) / 0.75))


@dataclass
class Chunk:
    text: str
    page_start: int
    page_end: int
    source: str = "prose"   # "prose" | "table"


def _cell(v: str | None) -> str:
    return re.sub(r"\s+", " ", (v or "").strip())


def table_to_chunks(table: list[list[str | None]], page_no: int) -> list[Chunk]:
    """One Chunk per non-empty data row. Header row used as column labels."""
    if not table:
        return []

    # Detect header: first row where all non-None cells are non-empty
    header: list[str] | None = None
    data_start = 0
    first = [_cell(c) for c in table[0]]
    if all(first) and len(first) >= MIN_TABLE_COLS:
        header = first
        data_start = 1

    chunks: list[Chunk] = []
    for row in table[data_start:]:
        cells = [_cell(c) for c in row]
        non_empty = [(i, v) for i, v in enumerate(cells) if v]
        if len(non_empty) < 1:
            continue
        if header:
            parts = [
                f"{header[i]}: {v}" if i < len(header) else v
                for i, v in non_empty
            ]
        else:
            parts = [v for _, v in non_empty]
        text = ". ".join(parts)
        if len(text) >= 20:
            chunks.append(Chunk(text=text, page_start=page_no, page_end=page_no, source="table"))
    return chunks


def chunk_prose(pages: list[PageData]) -> list[Chunk]:
    """Sentence-pack prose text from pages that have no tables."""
    sentences: list[tuple[int, str]] = []
    for p in pages:
        if not p.text.strip():
            continue
        for raw in re.split(r"(?<=[\.\!\?])\s+|\n{2,}", p.text):
            s = raw.strip()
            if s:
                sentences.append((p.page_no, s))
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


def chunk_pages(pages: list[PageData]) -> list[Chunk]:
    """Route each page: table pages → row-per-chunk; prose pages → sentence packing."""
    table_chunks: list[Chunk] = []
    prose_pages: list[PageData] = []

    for p in pages:
        if p.tables:
            for table in p.tables:
                table_chunks.extend(table_to_chunks(table, p.page_no))
        else:
            prose_pages.append(p)

    prose_chunks = chunk_prose(prose_pages)
    return table_chunks + prose_chunks


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
    print(f"[ingest_pdfs] TARGET_TOKENS={TARGET_TOKENS} OVERLAP_TOKENS={OVERLAP_TOKENS}")

    print("[ingest_pdfs] loading encoder...")
    encoder = load_default_encoder()
    retriever = VectorRetriever(encoder)
    print(f"[ingest_pdfs] db_path={retriever.db_path} dim={retriever.vector_size}")
    retriever.reset_collection(CHUNKS_COLLECTION)

    total_chunks = 0
    table_total = 0
    prose_total = 0
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
                    "section": ch.source,
                    "text": ch.text,
                }
            )
        if not items:
            continue
        t_count = sum(1 for c in chunks if c.source == "table")
        p_count = sum(1 for c in chunks if c.source == "prose")
        table_total += t_count
        prose_total += p_count
        retriever.upsert_chunks(items)
        total_chunks += len(items)
        print(f"  {len(items)} chunks (table={t_count} prose={p_count}) cumulative={total_chunks}")

    dt = time.time() - t0
    print(f"[ingest_pdfs] done: {total_chunks} chunks (table={table_total} prose={prose_total}) in {dt:.1f}s")
    print(f"[ingest_pdfs] collection size: {retriever.count(CHUNKS_COLLECTION)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
