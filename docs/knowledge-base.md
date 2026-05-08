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

- **Runtime source**: Convex cloud (`kb_entries` table, `https://elated-ibis-809.eu-west-1.convex.cloud`)
- **Local fallback**: `data/knowledge_base.json` (loaded if Convex unavailable)
- **Convex functions**: `kb:listEntries`, `kb:listCategories`, `kb:getByCategory`, `kb:getByModel`

## Stats

- **290 entries** total
- **9 categories**
- **81 unique machine models** + `universal` entries (apply to all machines)

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

## Keywords

Each entry has multilingual keywords (Ukrainian + Russian + English) for broad matching:
- `"E01"`, `"помилка E01"`, `"ошибка E01"`, `"error E01"`, `"overheat"`

This allows users to write in any mix of languages and still match correctly.

## Model Field

- `"universal"` — applies to all machines
- Specific slug (e.g. `"delonghi_magnifica_s"`) — model-specific advice
- Classifier prefers model-specific entries when user has set their machine

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
```
