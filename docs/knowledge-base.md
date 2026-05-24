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
| **Qdrant `kb_qa`** | Error-code override path | ~72 curated entries (manual-extracts excluded) | `scripts/ingest_kb.py` |
| **Qdrant `kb_chunks`** | Primary retrieval path | 71,869 PDF manual chunks | `scripts/ingest_pdfs.py` / `scripts/upload_vectors.py` |

Convex remains the canonical source. The two Qdrant collections are derived caches hosted on Qdrant Cloud. Bot connects to:
- Convex: `https://elated-ibis-809.eu-west-1.convex.cloud` (configured via `CONVEX_URL` env var).
- Qdrant Cloud: `eu-central-1-0.aws.cloud.qdrant.io` (configured via `QDRANT_URL` + `QDRANT_API_KEY`).

## Retrieval Path

`kb_qa` is used **only** when an error-code regex fires (`CONFIDENCE_THRESHOLD = 1.0`). All other queries go directly to the chunk cascade over `kb_chunks`. This means `kb_qa` acts as a precision override for known error codes, while 71,869 manual chunks handle all other queries.

## Stats

- **290 entries** total in Convex / JSON.
- **~72 entries** indexed into `kb_qa` (curated only — see "Manual-extract filter" below).
- **71,869 chunks** in `kb_chunks` (full PDF ingest, table-aware extraction).
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

A `manual` virtual category is emitted at runtime when the answer comes from `kb_chunks` rather than a curated entry.

## Manual-extract filter

Entries auto-generated from PDF sections (id suffixes `_specs`, `_settings`, `_cleaning_uk`, `_cleaning_en`, `_brewing_001`, or with `"Інструкція для"` boilerplate in question/answer) are excluded from `kb_qa` by `Classifier._is_manual_extract`. The same content is indexed at chunk granularity in `kb_chunks` and surfaced through the cascade.

## Keywords

Each entry has multilingual keywords (Ukrainian + Russian + English) for broad matching:
- `"E01"`, `"помилка E01"`, `"ошибка E01"`, `"error E01"`, `"overheat"`.

The keyword boost is applied on top of the Qdrant cosine score in the classifier.

## Model Field

- `"universal"` — applies to all machines.
- Specific slug (e.g. `"delonghi_magnifica_s"`) — model-specific advice.
- Classifier pushes the model filter into Qdrant; the user's machine model and `universal` are surfaced first.
- Chunk fallback derives the **brand** from the model slug via longest-prefix match against `retriever.list_brands()` (e.g. `russell_hobbs_24370` → `russell_hobbs`).

## Brand Validation

When a user enters their machine model during onboarding, the bot resolves the brand via longest-prefix match against `assistant.known_brands`. If the brand is unknown (e.g. "Samsung"), the bot rejects the input with a friendly message and offers to use `universal` mode instead. This prevents silently accepting non-coffee-machine brands and then serving mismatched manual content.

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

# Rebuild kb_qa in Qdrant after any KB change
python3 scripts/ingest_kb.py
```
