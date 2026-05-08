# Data Pipeline

## Overview

```
PDF manuals (263, 1.6GB)
     │
     ▼ scripts/parse_manual.py
data/manuals/_parsed/*.json  (263 files, 25MB) ← gitignored
     │                                            (PDFs also gitignored)
     ▼ scripts/manual_to_kb.py
data/knowledge_base.json  (290 entries, 1.1MB) ← tracked in git
     │
     ▼ scripts/seed_convex.py
Convex cloud — kb_entries table (290 rows) ← runtime source
```

## Step 1 — Parse PDFs (`scripts/parse_manual.py`)

Extracts structured data from each PDF manual:
- `brand`, `model`, `title`, `language`, `page_count`
- `raw_text` — full extracted text
- `sections` — detected sections (cleaning, errors, brewing, etc.)
- `error_codes` — parsed error code table

Output: `data/manuals/_parsed/{brand}_{filename}.json`

## Step 2 — Generate KB Entries (`scripts/manual_to_kb.py`)

Converts parsed JSON → KB entries, two modes:
- **auto** (default): extracts error codes only → `error_code` category entries
- **--with-sections**: also wraps each manual section as a KB entry

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
| `data/knowledge_base.json` | Yes | KB categories list |
| `data/manuals/_parsed/` | No | Derived from PDFs; PDFs not in repo |
| `data/manuals/**/*.pdf` | No | 1.6GB, Convex has the extracted data |
