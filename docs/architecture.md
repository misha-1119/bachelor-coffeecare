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
│ goodbye, followup,   │  │ Qdrant search_qa     │  │ Rewrites retrieved  │
│ safety, more detail  │  │ + keyword boost      │  │ text into a short   │
│ → instant reply      │  │ + error code override│  │ Ukrainian reply     │
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
                  │  curated entries   │  │ PDF chunks (page +   │
                  │  (manual extracts  │  │ brand metadata)      │
                  │  excluded)         │  │                      │
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
| Qdrant local (file-backed) | Persistent vector index over kb_qa + kb_chunks; HNSW search, payload filters; zero infra |
| Two Qdrant collections | kb_qa is curated and high-precision; kb_chunks is raw PDF text, used only as fallback |
| liberta-large (Ukrainian BERT) | Best semantic understanding of Ukrainian; shared encoder for both collections |
| Encoder on CPU by default | Avoids MPS OOM when Ollama is also using the GPU; override via ENCODER_DEVICE |
| Lapa LLM (Gemma-3-12B, Ukrainian) | Best Ukrainian generation; ~1.5x faster tokenisation than vanilla Gemma |
| 3-tier chunk cascade | Exact model → same brand → universal; salvages recall when the user's exact PDF is sparse |
| Confidence threshold 0.55 | Below this → try chunk fallback, then clarify; avoid wrong answers |
| Chunk threshold 0.45 | Lower because raw manual text scores lower than curated questions |

## User State (in-memory)

- `USER_STATE[user_id]` — machine model + conversation stage
- `USER_MODE[user_id]` — `"nlp"` (default) or `"rule_based"`
- `USER_CONVERSATION[user_id]` — last KB entry ID + tried IDs (for "no, try again" flow)

## Concurrency note

Local Qdrant holds an **exclusive file lock** on `data/qdrant/`. The bot cannot run while `scripts/ingest_kb.py` or `scripts/ingest_pdfs.py` are active. If the retriever can't open the DB at boot, the assistant silently falls back to the legacy in-memory numpy path over `KnowledgeBase`.
