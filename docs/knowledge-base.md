# Knowledge Base

## Structure

Each KB entry is a curated Q&A pair:

```json
{
  "id": "kb_001",
  "category": "error_code",
  "keywords": ["E01", "помилка E01", "перегрів", "overheat", ...],
  "question": "Що означає помилка E01?",
  "answer": "Step-by-step Ukrainian solution...",
  "model": "universal"
}
```

## Storage

Two layers serve different jobs:

| Store | Purpose | Contents | Updated by |
|-------|---------|----------|------------|
| **Convex `kb_entries`** | Source of truth, edited live | All 290 entries | `seed_convex.py`, `add_kb_entry.py` |
| **JSON `data/knowledge_base.json`** | Local fallback when Convex is down | Same 290 entries | git-tracked |
| **Qdrant `kb_qa`** | Runtime semantic index | ~72 curated entries (manual-extracts excluded) | `scripts/ingest_kb.py` |
| **Qdrant `kb_chunks`** | Runtime fallback for PDF content | Thousands of PDF chunks | `scripts/ingest_pdfs.py` |

Convex remains the canonical source. The two Qdrant collections are derived caches — drop and rebuild from `ingest_kb.py` / `ingest_pdfs.py`. Bot connects to:
- Convex: `https://elated-ibis-809.eu-west-1.convex.cloud`.
- Convex functions: `kb:listEntries`, `kb:listCategories`, `kb:getByCategory`, `kb:getByModel`.
- Qdrant: local file-backed DB at `data/qdrant/` (path overridable via `QDRANT_PATH`).

## Stats

- **290 entries** total in Convex / JSON.
- **~72 entries** indexed into `kb_qa` (curated only — see "Manual-extract filter" below).
- **~thousands of chunks** in `kb_chunks` after full PDF ingest (one chunk per ~600 tokens of manual text).
- **9 categories**.
- **81 unique machine models** + `universal` entries (apply to all machines).
- **21 brands** with PDF manuals (derived from `data/manuals/<brand>/` directories; queryable via `retriever.list_brands()`).

## Categories

| Category | Count | Description |
|----------|-------|-------------|
| `cleaning` | 109 | Descaling, brew group cleaning, milk system |
| `general` | 102 | Setup, water, beans, general usage |
| `error_code` | 27 | E01–E20 and brand-specific error codes |
| `brewing` | 22 | Weak coffee, no crema, temperature, grind |
| `clarify` | 10 | Vague queries → bot asks clarifying question |
| `maintenance` | 8 | Long-term upkeep, filters, lubrication |
| `water_system` | 6 | No water, pump issues, leaks |
| `grinding` | 5 | Grinder jams, adjustment, noise |
| `no_coffee` | 1 | Machine not producing coffee |

A `manual` "virtual category" is emitted at runtime when the answer comes from `kb_chunks` rather than a curated entry.

## Manual-extract filter

Entries auto-generated from PDF sections (id suffixes `_specs`, `_settings`, `_cleaning_uk`, `_cleaning_en`, `_brewing_001`, or with `"Інструкція для"` boilerplate in question/answer) are excluded from `kb_qa` by `Classifier._is_manual_extract`. The reason: those entries are noisy and used to hijack short queries via spurious semantic similarity. The same content is now indexed at chunk granularity in `kb_chunks` and surfaced through the cascade described in `ai-pipeline.md`.

## Keywords

Each entry has multilingual keywords (Ukrainian + Russian + English) for broad matching:
- `"E01"`, `"помилка E01"`, `"ошибка E01"`, `"error E01"`, `"overheat"`.

This allows users to write in any mix of languages and still match correctly. The keyword boost is applied on top of the Qdrant cosine score in the classifier.

## Model Field

- `"universal"` — applies to all machines.
- Specific slug (e.g. `"delonghi_magnifica_s"`) — model-specific advice.
- Classifier pushes the model filter into Qdrant; the user's machine model and `universal` are surfaced first.
- Chunk fallback derives the **brand** from the model slug via longest-prefix match against `retriever.list_brands()` (e.g. `russell_hobbs_24370` → `russell_hobbs`).

## How to Add Entries

```bash
# Interactive: add one entry + push to Convex
python3 scripts/add_kb_entry.py

# Bulk: deduplicate and re-sync to Convex
python3 scripts/dedupe_kb.py

# From parsed manual: extract error codes → KB entries
python3 scripts/manual_to_kb.py --brand delonghi

# After editing knowledge_base.json manually:
python3 scripts/seed_convex.py

# Rebuild Qdrant after any KB change
python3 scripts/ingest_kb.py
```
