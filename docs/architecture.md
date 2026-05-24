# System Architecture

## Components

```
┌─────────────────────────────────────────────────────────────┐
│                        Telegram                             │
│                     (user interface)                        │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                       bot.py                                │
│  - Telegram handlers (commands + messages)                  │
│  - User state: machine model, conversation history, mode    │
│  - Button keyboard: machine, errors, tips, help, reset      │
└──────────┬──────────────────────────────┬───────────────────┘
           │                              │
           ▼                              ▼
┌────────────────────┐       ┌────────────────────────┐
│  RuleBasedAssistant│       │   CoffeeBotAssistant   │
│  (fallback/eval)   │       │   (default NLP mode)   │
│  keyword scoring   │       │   triage + classifier  │
│  against KB        │       │   + chunk cascade      │
└────────────────────┘       │   + generator          │
                             └──────────┬─────────────┘
                                        │
               ┌────────────────────────┼──────────────────────┐
               │                        │                      │
               ▼                        ▼                      ▼
┌──────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│      triage.py       │  │    classifier.py     │  │    generator.py     │
│ Detect: greeting,    │  │ liberta-large encoder│  │ Lapa LLM via Ollama │
│ goodbye, followup,   │  │ error-code regex     │  │ Rewrites retrieved  │
│ safety, more detail  │  │ → kb_qa only on      │  │ text into a short   │
│ → instant reply      │  │   regex hit (conf=1) │  │ Ukrainian reply     │
└──────────────────────┘  └──────────┬───────────┘  └──────────┬──────────┘
                                     │                          │
                                     ▼                          │
                          ┌────────────────────────┐            │
                          │      retriever.py       │            │
                          │  VectorRetriever        │◄───────────┘
                          │  (shared liberta + lru) │
                          └────┬──────────────┬─────┘
                               │              │
                               ▼              ▼
                  ┌────────────────────┐  ┌──────────────────────┐
                  │  Qdrant: kb_qa     │  │ Qdrant: kb_chunks    │
                  │  curated entries   │  │ 71,869 PDF chunks    │
                  │  (error-code hits  │  │ (brand + page meta)  │
                  │   only)            │  │ PRIMARY retrieval    │
                  └─────────┬──────────┘  └──────────────────────┘
                            │
                            ▼
                  ┌────────────────────┐
                  │  KnowledgeBase     │
                  │  290 Q&A entries   │
                  │  Convex (runtime)  │
                  │  JSON (fallback)   │
                  └─────────┬──────────┘
                            │
                            ▼
                  ┌──────────────────────┐
                  │   Convex Cloud       │
                  │  kb_entries / users  │
                  └──────────────────────┘
```

## Key Design Decisions

| Decision | Reason |
|----------|--------|
| Convex as KB source of truth | Live updates without redeploy; fallback to local JSON |
| Qdrant Cloud | Persistent vector index accessible from Railway; no local file lock issues |
| `kb_chunks` as primary path | 71,869 PDF chunks cover model-specific detail; curated `kb_qa` only used for error-code regex overrides |
| CONFIDENCE_THRESHOLD = 1.0 | Only error-code regex hits (conf=1.0) take the `kb_qa` path; all other queries go directly to chunk cascade — avoids wrong curated answers |
| liberta-large (Ukrainian BERT) | Best semantic understanding of Ukrainian; shared encoder for both collections |
| Liberta baked into Docker image | Prevents repeated HuggingFace downloads on cold start / Railway restarts |
| Encoder on CPU by default | Avoids MPS OOM when Ollama is also using the GPU; override via `ENCODER_DEVICE` |
| Lapa LLM (Gemma-3-12B, Ukrainian) | Best Ukrainian generation; ~1.5x faster tokenisation than vanilla Gemma |
| Lapa via ngrok tunnel (Railway) | Railway bot calls local Ollama through an ngrok HTTPS tunnel; `OLLAMA_URL` env var points to ngrok URL |
| 3-tier chunk cascade | Exact model → same brand → universal; salvages recall when the user's exact PDF is sparse |
| Chunk threshold 0.45 | Raw manual text scores lower than curated questions; env-tunable via `CHUNK_THRESHOLD` |
| Brand validation at onboarding | Unknown brand (e.g. "Samsung") is rejected with a friendly message rather than silently accepted |
| Clarify-followup merging | When last turn was `category=clarify`, next user message is merged with the original query before re-classification |

## User State (in-memory)

- `USER_STATE[user_id]` — machine model + conversation stage
- `USER_MODE[user_id]` — `"nlp"` (default) or `"rule_based"`
- `USER_CONVERSATION[user_id]` — last KB entry ID, tried IDs, last chunk, pending clarify query

## Deployment

| Component | Platform | Notes |
|-----------|----------|-------|
| Bot | Railway | Docker image, auto-deploy from `main` branch; needs 2 GB RAM for liberta |
| Qdrant | Qdrant Cloud | `eu-central-1-0.aws.cloud.qdrant.io`; `QDRANT_URL` + `QDRANT_API_KEY` env vars |
| Lapa LLM | Local (ngrok) | `./scripts/start_ollama_tunnel.sh` — starts Ollama Docker + ngrok; paste printed `OLLAMA_URL` into Railway env |
| Local demo | Docker Compose | `./run_local.sh` — starts qdrant + ollama + bot; bot reads Qdrant Cloud URL from `.env` |
