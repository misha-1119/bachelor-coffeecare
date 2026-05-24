# Data Pipeline

## Overview

```
PDF manuals (263, 1.6GB)
     │
     └─► scripts/ingest_pdfs.py ──► Qdrant Cloud kb_chunks (71,869 chunks, page metadata)
                                          │
                                          └── pre-encoded vectors available locally at
                                              data/vectors/ → scripts/upload_vectors.py

data/knowledge_base.json  (290 entries, 1.1MB)  ── tracked
     │
     ├─► scripts/seed_convex.py ──► Convex kb_entries (runtime source for KnowledgeBase)
     │
     └─► scripts/ingest_kb.py ──► Qdrant Cloud kb_qa  (curated entries; manual-extracts skipped)
```

The `kb_chunks` collection (71,869 vectors) is already uploaded to Qdrant Cloud. Standard usage requires no re-ingestion. Re-run `ingest_pdfs.py` only when new PDFs are added.

## Step 1 — Index PDF chunks into Qdrant (`scripts/ingest_pdfs.py`)

Walks `data/manuals/<brand>/*.pdf`, extracts text per page with `pdfplumber` (table-aware; fallback: `pypdf`), packs sentences into ~600-token chunks with 100-token overlap, derives `brand` from parent folder and `model` from filename slug, encodes with liberta-large, upserts into Qdrant collection `kb_chunks`.

```bash
python3 scripts/ingest_pdfs.py                # full run (~hours on CPU)
python3 scripts/ingest_pdfs.py --limit 20      # smoke test on first 20 PDFs
```

If the `data/vectors/` directory exists with pre-encoded `.npz` files, use `upload_vectors.py` to skip re-encoding:

```bash
QDRANT_URL=https://... QDRANT_API_KEY=... python3 scripts/upload_vectors.py
```

## Step 2 — Index curated KB into Qdrant (`scripts/ingest_kb.py`)

Loads `KnowledgeBase` (Convex first, JSON fallback), filters out manual-extract entries via `_is_manual_extract`, encodes with liberta-large, upserts into Qdrant collection `kb_qa`. Idempotent — drops and recreates the collection on each run.

```bash
python3 scripts/ingest_kb.py
```

## Step 3 — Seed Convex (`scripts/seed_convex.py`)

Reads `knowledge_base.json` → clears Convex tables → inserts all entries fresh.

```bash
python3 scripts/seed_convex.py
```

## Legacy Scripts (PDF → JSON → KB)

These are no longer part of the main flow but kept for reference:

| Script | Purpose |
|--------|---------|
| `scripts/parse_manual.py` | Extract structured data from PDFs → `data/manuals/_parsed/*.json` |
| `scripts/manual_to_kb.py` | Convert parsed JSON sections → `knowledge_base.json` entries |

## Maintenance Scripts

| Script | Purpose |
|--------|---------|
| `scripts/add_kb_entry.py` | Interactive: add single Q&A entry → JSON + Convex |
| `scripts/dedupe_kb.py` | Remove duplicate entries, re-sync to Convex |
| `scripts/scrape_manuals.py` | Scrape new PDF manuals from manufacturer sites |
| `scripts/scrape_manualslib.py` | Scrape manuals from manualslib.com |
| `scripts/start_ollama_tunnel.sh` | Start Ollama Docker + ngrok tunnel; prints Railway `OLLAMA_URL` |

## What's Tracked in Git

| Path | Tracked | Reason |
|------|---------|--------|
| `data/knowledge_base.json` | Yes | Seed source + local fallback |
| `data/test_queries.json` | Yes | Retrieval evaluation set |
| `data/manuals/_parsed/` | No | Derived from PDFs |
| `data/manuals/**/*.pdf` | No | 1.6 GB, not redistributable |
| `data/vectors/` | No | Pre-encoded vectors; too large for git |

## Qdrant Cloud

Both `kb_qa` and `kb_chunks` live on Qdrant Cloud (`eu-central-1-0.aws.cloud.qdrant.io`). Access requires:

```
QDRANT_URL=https://<cluster-id>.eu-central-1-0.aws.cloud.qdrant.io
QDRANT_API_KEY=<key>
```

All ingest scripts and the bot read these from environment variables. No local Qdrant instance is needed for standard usage.
