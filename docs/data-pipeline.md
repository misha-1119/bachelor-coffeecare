# Data Pipeline

## Overview

```
PDF manuals (263, 1.6GB)
     │
     ├─► scripts/parse_manual.py ──► data/manuals/_parsed/*.json (legacy structured extracts) ── gitignored
     │
     └─► scripts/ingest_pdfs.py ──► Qdrant kb_chunks (text chunks, page metadata)

data/knowledge_base.json  (290 entries, 1.1MB)  ── tracked
     │
     ├─► scripts/seed_convex.py ──► Convex kb_entries (runtime source for KnowledgeBase)
     │
     └─► scripts/ingest_kb.py ──► Qdrant kb_qa  (only curated entries; manual-extracts skipped)
```

`scripts/manual_to_kb.py` is the legacy bridge that converts parsed JSON sections into `knowledge_base.json` entries. With `scripts/ingest_pdfs.py` now indexing chunks directly into Qdrant, manual-extract entries in `knowledge_base.json` are excluded from `kb_qa` and the bot serves manual content from `kb_chunks` instead.

## Step 1 — Parse PDFs (`scripts/parse_manual.py`)

Extracts structured data from each PDF manual (used for the legacy JSON pipeline and for evaluating retrieval):
- `brand`, `model`, `title`, `language`, `page_count`
- `raw_text` — full extracted text
- `sections` — detected sections (cleaning, errors, brewing, etc.)
- `error_codes` — parsed error code table

Output: `data/manuals/_parsed/{brand}_{filename}.json`

## Step 2 — Generate KB Entries (`scripts/manual_to_kb.py`)

Converts parsed JSON → KB entries, two modes:
- **auto** (default): extracts error codes only → `error_code` category entries.
- **--with-sections**: also wraps each manual section as a KB entry (these are the noisy entries skipped from `kb_qa`).

Appends to `data/knowledge_base.json`, optionally pushes to Convex.

```bash
python3 scripts/manual_to_kb.py                  # all brands, error codes only
python3 scripts/manual_to_kb.py --brand delonghi  # single brand
python3 scripts/manual_to_kb.py --with-sections   # include sections
python3 scripts/manual_to_kb.py --dry-run         # preview, no write
```

## Step 3 — Seed Convex (`scripts/seed_convex.py`)

Reads `knowledge_base.json` → clears Convex tables → inserts all entries fresh.

```bash
python3 scripts/seed_convex.py
```

## Step 4 — Index curated KB into Qdrant (`scripts/ingest_kb.py`)

Loads `KnowledgeBase` (Convex first, JSON fallback), filters out manual-extract entries via `_is_manual_extract`, encodes with liberta-large, upserts into Qdrant collection `kb_qa`. Idempotent — drops and recreates the collection on each run.

```bash
python3 scripts/ingest_kb.py
```

## Step 5 — Index PDF chunks into Qdrant (`scripts/ingest_pdfs.py`)

Walks `data/manuals/<brand>/*.pdf` (skips `_parsed/`), extracts text per page with `pdfplumber` (fallback: `pypdf`), packs sentences into ~600-token chunks with 100-token overlap, derives `brand` from parent folder and `model` from filename slug, encodes, upserts into Qdrant collection `kb_chunks`. Idempotent.

```bash
python3 scripts/ingest_pdfs.py                # full run (~hours on CPU)
python3 scripts/ingest_pdfs.py --limit 20      # smoke test on first 20 PDFs
```

The encoder defaults to **CPU** to avoid MPS OOM contention with Ollama. Override with `ENCODER_DEVICE=mps` when no other GPU load is running.

## Maintenance Scripts

| Script | Purpose |
|--------|---------|
| `scripts/add_kb_entry.py` | Interactive: add single Q&A entry → JSON + Convex |
| `scripts/dedupe_kb.py` | Remove duplicate entries, re-sync to Convex |
| `scripts/scrape_manuals.py` | Scrape new PDF manuals from manufacturer sites |
| `scripts/scrape_manualslib.py` | Scrape manuals from manualslib.com |

## What's Tracked in Git

| Path | Tracked | Reason |
|------|---------|--------|
| `data/knowledge_base.json` | Yes | Seed source + local fallback |
| `data/test_queries.json` | Yes | Retrieval evaluation set |
| `data/manuals/_parsed/` | No | Derived from PDFs |
| `data/manuals/**/*.pdf` | No | 1.6GB, not redistributable |
| `data/qdrant/` | No | Local vector index; rebuild with the ingest scripts |
