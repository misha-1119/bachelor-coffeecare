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
│  against KB        │       │   + generator          │
└────────────────────┘       └──────────┬─────────────┘
                                        │
               ┌────────────────────────┼──────────────────────┐
               │                        │                      │
               ▼                        ▼                      ▼
┌──────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│      triage.py       │  │    classifier.py     │  │    generator.py     │
│ Detect: greeting,    │  │ liberta-large BERT   │  │ Lapa LLM via Ollama │
│ goodbye, followup,   │  │ Cosine similarity    │  │ Rewrites KB answer  │
│ safety, more detail  │  │ + keyword boost      │  │ into conversational │
│ → instant reply      │  │ + error code override│  │ Ukrainian response  │
└──────────────────────┘  └──────────┬──────────┘  └──────────┬──────────┘
                                     │                         │
                                     ▼                         │
                          ┌──────────────────────┐            │
                          │    KnowledgeBase      │◄───────────┘
                          │  290 Q&A entries      │
                          │  Loaded from Convex   │
                          │  (fallback: JSON)     │
                          └──────────┬────────────┘
                                     │
                                     ▼
                          ┌──────────────────────┐
                          │   Convex Cloud        │
                          │  kb_entries table     │
                          │  categories table     │
                          └──────────────────────┘
```

## Key Design Decisions

| Decision | Reason |
|----------|--------|
| Convex as KB store | Live updates without redeploy; fallback to local JSON |
| liberta-large (Ukrainian BERT) | Best semantic understanding of Ukrainian text |
| Lapa LLM (Gemma-3-12B, Ukrainian) | Best Ukrainian generation; 1.5x faster tokenization |
| Two modes: NLP + rule-based | Rule-based for eval baseline, NLP for production |
| Confidence threshold 0.55 | Below this → ask user to clarify, avoid wrong answers |

## User State (in-memory)

- `USER_STATE[user_id]` — machine model + conversation stage
- `USER_MODE[user_id]` — `"nlp"` (default) or `"rule_based"`
- `USER_CONVERSATION[user_id]` — last KB entry ID + tried IDs (for "no, try again" flow)
