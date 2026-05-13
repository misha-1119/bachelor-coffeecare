# AI Pipeline

## Flow

```
User message
     │
     ▼
[triage.py] ─── greeting/goodbye/safety/followup? ──► instant reply
     │ no
     ▼
[classifier.py — Stage 1: liberta-large encoder + Qdrant retriever]
  1. Error code override: if query matches \be\s*0?(\d{1,2})\b → return exact KB entry
  2. Encode query → 1024-dim vector (lru_cache on normalised query)
  3. Qdrant search kb_qa (HNSW + payload filter on `model`), top-10
  4. Apply keyword boost on top-10 (+0.15 phrase, +0.05 long-token, +0.04 stem; max +0.45)
  5. Pick best entry; legacy numpy path used only when retriever is unavailable
     │
     ├── confidence ≥ 0.55 ──► [generator.py — Stage 2: Lapa LLM]
     │
     └── confidence < 0.55 ──► [assistant.py — chunk fallback cascade]
              │
              ├── Tier 1: kb_chunks filtered by exact `model` slug + universal
              ├── Tier 2: kb_chunks filtered by `brand` (longest-prefix match)
              ├── Tier 3: kb_chunks unfiltered (any model)
              │
              ├── best chunk score ≥ CHUNK_THRESHOLD (0.45) ──► generator with chunk text + page citation
              │
              └── no chunk above threshold ──► clarify fallback
```

## Stage 1: Retrieval (liberta-large + Qdrant)

- **Encoder**: `Goader/liberta-large` — Ukrainian BERT, 1024-dim, normalised.
  - Fallback: `paraphrase-multilingual-MiniLM-L12-v2` if primary fails to load.
  - Device default: **CPU** (`ENCODER_DEVICE=cpu`). Override with `mps`/`cuda` if no GPU contention (Ollama on MPS will OOM otherwise).
- **Storage**: local file-backed Qdrant at `data/qdrant/`.
  - `kb_qa` — curated entries only (manual extracts excluded via `_is_manual_extract`).
  - `kb_chunks` — PDF manual chunks with `brand`, `model`, `file`, `page_start/end` payload.
- **Encoded text** for `kb_qa`: `"{category}: {question} | {first 10 keywords}"`.
- **Model filter** pushed into Qdrant as a `should` filter on `model == user_slug OR == "universal"`.
- **Error code override** runs before vector search; regex `\be\s*0?(\d{1,2})\b` short-circuits to a deterministic KB entry.

## Chunk fallback cascade

When QA confidence is below `CONFIDENCE_THRESHOLD` (0.55), assistant tries `kb_chunks`:

| Tier | Filter | Citation format |
|------|--------|-----------------|
| 1 | `model == user_slug OR universal` | `Джерело: <file>, стор. N` |
| 2 | `brand == <derived>`, excluding tier-1 model | `Джерело зі схожої моделі (<hit_model>): <file>, стор. N` |
| 3 | none (any model) | `Джерело: <file>, стор. N` |

Cascade stops at the first tier with `best.score ≥ CHUNK_THRESHOLD` (env-tunable, default 0.45). Brand is derived from the user's model slug via longest-prefix match against `retriever.list_brands()` cached at boot.

## Stage 2: Generator (Lapa LLM)

- **Model**: `hf.co/lapa-llm/lapa-v0.1.2-instruct-GGUF` (Gemma-3-12B, Ukrainian-tuned).
- **Runtime**: Ollama at `http://localhost:11434`.
- **Parameters**: temperature 0.3, max 160 tokens, top_p 0.9, context 1024.
- **System prompt rules**:
  - Only Ukrainian, conversational tone.
  - 2–4 sentences max.
  - No headers, numbered lists, markdown, emoji.
  - Rephrase KB / chunk text — never copy verbatim.
  - Address user by name once (if known).
  - End with an open question ("Допомогло?", "Що показує машина?").
  - Never invent facts outside the retrieved instruction.
- **Offline fallback**: returns first paragraph of KB answer (or chunk text for manual hits), truncated to 320 chars.

## Triage Rules

| Trigger | Action |
|---------|--------|
| Greeting (привіт, hi, ...) | Friendly greeting reply |
| Goodbye (дякую, bye, ...) | Closing reply |
| "допомогло" / yes | Positive acknowledgement |
| "не спрацювало" / no | Mark last entry as tried, offer retry |
| "детальніше" / more detail | Return full KB answer for last entry |
| Urgent safety keywords | Immediate "unplug the machine" safety reply |
| Negative meta ("не те", "ти не допоміг") | Acknowledge, re-ask |

## Rule-Based Mode (baseline)

Used for evaluation comparison. Simple keyword scoring:
- Score = sum of keyword matches / number of keywords.
- No embeddings, no LLM — pure lexical matching.
- Returns raw KB answer without rephrasing.

## Evaluation

`evaluation/eval_retrieval.py` benchmarks three retrieval variants on `data/test_queries.json`:
- `numpy` — legacy in-memory cosine over the full KB.
- `qdrant` — Qdrant `kb_qa` only.
- `qdrant_chunks` — `kb_qa` + chunk splice when QA score is weak.

Reports Recall@1, Recall@5, MRR, latency p50/p95 in a Markdown table.
