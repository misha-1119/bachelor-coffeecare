# AI Pipeline

## Flow

```
User message
     │
     ▼
[triage.py] ─── greeting/goodbye/safety/followup? ──► instant reply
     │ no
     │
     ├── pending_clarify_query in conversation?
     │     yes → merge: "{original_clarify_query}. {current_message}"
     │
     ▼
[classifier.py — Stage 1: liberta-large encoder + Qdrant retriever]
  1. Error code override: if query matches \be\s*0?(\d{1,2})\b → return exact KB entry (conf=1.0)
  2. Encode query → 1024-dim vector (lru_cache on normalised query)
  3. Qdrant search kb_qa (HNSW + payload filter on `model`), top-10
  4. Apply keyword boost on top-10 (+0.15 phrase, +0.05 long-token, +0.04 stem; max +0.45)
  5. Pick best entry; legacy numpy path used only when retriever is unavailable
     │
     ├── confidence = 1.0 (error-code regex hit) ──► [generator.py — Stage 2: Lapa LLM]
     │
     └── confidence < 1.0 (all other queries) ──► [assistant.py — chunk fallback cascade]
              │
              ├── Tier 1: kb_chunks filtered by exact `model` slug + universal
              ├── Tier 2: kb_chunks filtered by `brand` (longest-prefix match)
              ├── Tier 3: kb_chunks unfiltered (only when no brand resolved)
              │
              ├── best chunk score ≥ CHUNK_THRESHOLD (0.45) ──► generator with chunk text + page citation
              │
              └── no chunk above threshold ──► clarify fallback
```

## Clarify-Followup Merging

When the bot returns a `category=clarify` answer (asking for more detail), the original query is stored as `conversation["pending_clarify_query"]`. On the next non-triage user message, the bot prepends the original query and re-classifies the merged string:

```
"блимає якась іконка"  →  clarify answer (which icon?)
"крапля води"          →  merged: "блимає якась іконка. крапля води"
                          → classified against KB → hits water-indicator entry
```

The pending query is consumed exactly once and cleared regardless of the result.

## Stage 1: Retrieval (liberta-large + Qdrant)

- **Encoder**: `Goader/liberta-large` — Ukrainian BERT, 1024-dim, normalised.
  - Baked into the Docker image (no HuggingFace download at runtime).
  - Device default: **CPU** (`ENCODER_DEVICE=cpu`). Override with `mps`/`cuda` if no GPU contention.
- **Storage**: Qdrant Cloud (`eu-central-1-0.aws.cloud.qdrant.io`); configured via `QDRANT_URL` + `QDRANT_API_KEY`.
  - `kb_qa` — curated entries; used only when error-code regex fires.
  - `kb_chunks` — 71,869 PDF manual chunks with `brand`, `model`, `file`, `page_start/end` payload. Primary retrieval path.
- **Encoded text** for `kb_qa`: `"{category}: {question} | {first 10 keywords}"`.
- **Model filter** pushed into Qdrant as a `should` filter on `model == user_slug OR == "universal"`.
- **Error code override** runs before vector search; regex `\be\s*0?(\d{1,2})\b` short-circuits to a deterministic KB entry with confidence=1.0.
- **CONFIDENCE_THRESHOLD = 1.0**: Only error-code regex hits (conf=1.0) use `kb_qa` directly. All other queries skip to the chunk cascade regardless of `kb_qa` score.

## Chunk Fallback Cascade

Primary retrieval path for all non-error-code queries:

| Tier | Filter | When it runs | Citation format |
|------|--------|--------------|-----------------|
| 1 | `model == user_slug OR universal` | always | `Джерело: <file>, стор. N` |
| 2 | `brand == <derived>`, excluding tier-1 model | brand resolved from user model slug | `Джерело зі схожої моделі (<hit_model>): <file>, стор. N` |
| 3 | none (any model) | **only when no brand resolved** — surfacing a different vendor's instructions to a user with a known machine is worse than asking them to clarify | `Джерело: <file>, стор. N` |

Cascade stops at the first tier with `best.score ≥ CHUNK_THRESHOLD` (env-tunable, default 0.45). Brand is derived from the user's model slug via longest-prefix match against `retriever.list_brands()` cached at boot; if a user references an unknown brand, the cache is re-listed once.

The cascade honours `tried_chunk_ids` from the conversation state: chunks the user already saw via the "не спрацювало" flow are excluded with a Qdrant `must_not` filter on `chunk_id`.

## Stage 2: Generator (Lapa LLM)

- **Model**: `hf.co/lapa-llm/lapa-v0.1.2-instruct-GGUF` (Gemma-3-12B, Ukrainian-tuned).
- **Runtime**: Ollama; URL configured via `OLLAMA_URL` env var.
  - Local: `http://localhost:11434/api/generate` (Docker Compose wires this automatically).
  - Railway: ngrok HTTPS tunnel to local Ollama — run `./scripts/start_ollama_tunnel.sh`, paste printed URL into Railway `OLLAMA_URL`.
- **Availability check**: `DISABLE_LLAMA=1` disables LLM (Railway default when tunnel not active). Generator falls back to raw chunk/KB text with a "розкажи детальніше" footer.
- **Parameters**: temperature 0.3, max 160 tokens, top_p 0.9, context 1024.
- **System prompt rules**:
  - Only Ukrainian, conversational tone.
  - 2–4 sentences max.
  - No headers, numbered lists, markdown, emoji.
  - Rephrase KB / chunk text — never copy verbatim.
  - Address user by name once (if known).
  - End with an open question ("Допомогло?", "Що показує машина?").
  - Never invent facts outside the retrieved instruction.
- **Two prompt variants** (chosen by `category`):
  - `category != "manual"` — instruction is a curated KB answer; tail asks for 2–4 sentence rephrase.
  - `category == "manual"` — instruction is a raw PDF chunk; tail asks for 2–4 short steps and forces the model to admit when the chunk doesn't actually answer the query.
- **Offline fallback**: returns first paragraph of KB answer (or chunk text), truncated to 320 chars.

## Brand Validation

At onboarding, when the user enters their machine model, the bot resolves the brand via longest-prefix match against `assistant.known_brands`. If the brand is unknown (e.g. "Samsung"):

- Bot replies with a friendly message explaining no manuals exist for that brand.
- Offers two inline buttons: "Загальні поради" (use `universal`) or "Ввести іншу" (re-enter model).
- `set_machine` is NOT called until a valid brand is confirmed.

## Triage Rules

| Trigger | Action |
|---------|--------|
| Greeting (привіт, hi, ...) | Friendly greeting reply |
| Goodbye (дякую, bye, ...) | Closing reply |
| "допомогло" / yes | Positive acknowledgement |
| "не спрацювало" / no | Mark last entry as tried, offer retry |
| "детальніше" / more detail | Return full KB answer for last entry, or full PDF chunk + page citation |
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
- `qdrant_chunks` — chunk cascade (primary production path).

Reports Recall@1, Recall@5, MRR, latency p50/p95 in a Markdown table.
