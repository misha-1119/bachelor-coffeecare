# AI Pipeline

## Flow

```
User message
     │
     ▼
[triage.py] ─── greeting/goodbye/safety/followup? ──► instant reply
     │ no
     ▼
[classifier.py — Stage 1: liberta-large]
  1. Encode query → 1024-dim vector
  2. Cosine similarity vs all KB entry embeddings
  3. Add keyword boost (+0.15 per match, +0.04 per stem match, max +0.45)
  4. Apply machine model mask (hide entries for other models)
  5. Penalise auto-extracted PDF entries (−0.5, too noisy)
  6. Error code override: if query contains E01–E20 → force that KB entry
     │
     ├── confidence < 0.55 ──► ask user to clarify
     │
     └── confidence ≥ 0.55
              │
              ▼
       [generator.py — Stage 2: Lapa LLM via Ollama]
         Prompt: system prompt + KB answer + user query + user name
         Output: 2–4 sentence conversational reply in Ukrainian
         Fallback: truncate first paragraph of KB answer (if Ollama offline)
              │
              ▼
         Reply sent to user
```

## Stage 1: Classifier (liberta-large)

- **Model**: `Goader/liberta-large` — Ukrainian BERT, 1024-dim embeddings
- **Fallback model**: `paraphrase-multilingual-MiniLM-L12-v2` (if primary fails to load)
- **KB encoding**: each entry encoded as `"{category}: {question} | {keywords}"`
- **Scoring**: cosine similarity + keyword boost + model mask + manual extract penalty
- **Error code override**: regex `\be\s*0?(\d{1,2})\b` forces exact error code match

## Stage 2: Generator (Lapa LLM)

- **Model**: `hf.co/lapa-llm/lapa-v0.1.2-instruct-GGUF` (Gemma-3-12B, Ukrainian-tuned)
- **Runtime**: Ollama at `http://localhost:11434`
- **Parameters**: temperature 0.3, max 160 tokens, top_p 0.9, context 1024
- **System prompt rules**:
  - Only Ukrainian, conversational tone
  - 2–4 sentences max
  - No headers, numbered lists, markdown, emoji
  - Rephrase KB answer — never copy verbatim
  - Address user by name once (if known)
  - End with an open question ("Допомогло?", "Що показує машина?")
  - Never invent facts outside the KB answer
- **Offline fallback**: returns first paragraph of KB answer, truncated to 320 chars

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
- Score = sum of keyword matches / number of keywords
- No embeddings, no LLM — pure lexical matching
- Returns raw KB answer without rephrasing
