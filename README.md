# CaffeBot

Ukrainian-language Telegram chatbot for coffee machine troubleshooting. Users describe a problem in natural language; the bot finds the relevant page in the manufacturer's manual and replies in concise, conversational Ukrainian.

## Stack

| Layer | Tech |
|-------|------|
| Bot | python-telegram-bot, Railway (Docker) |
| Vector DB | Qdrant Cloud (eu-central-1), 71,869 PDF chunks |
| User/KB store | Convex Cloud |
| Encoder | `Goader/liberta-large` (Ukrainian BERT, 1024-dim, CPU) |
| LLM (primary) | Groq API — `llama-3.3-70b-versatile`, ~300 tok/s |
| LLM (fallback) | Lapa v0.1.2 (Gemma-3-12B, Ukrainian) via local Ollama |
| Local demo | `run_groq.sh` (Groq API) or `run_lapa.sh` (Docker Compose + Ollama) |

## How it works

1. **Triage** — instant rule-based replies for greetings, safety, follow-ups.
2. **Retrieval** — query encoded with liberta-large, searched against 71,869 PDF manual chunks in Qdrant with a 3-tier brand cascade (exact model → same brand → universal). Error-code queries (E01–E20) hit the curated KB directly.
3. **Generation** — retrieved chunk passed to Groq API (llama-3.3-70b-versatile); reply is 2–4 sentences, conversational Ukrainian, ends with an open question. Falls back to local Lapa via Ollama if Groq is unavailable, then to raw KB text.

## Run locally

### Groq mode (recommended — no Docker for LLM)

Requires: Python 3.12+, pip packages, `.env` with `GROQ_API_KEY`.

```bash
cp .env.example .env
# fill in: TELEGRAM_BOT_TOKEN, CONVEX_URL, QDRANT_URL, QDRANT_API_KEY, GROQ_API_KEY
pip install -r requirements.txt
./run_groq.sh
```

### Lapa mode (offline LLM via Docker Compose)

Requires: Docker Desktop, `.env` with credentials (no `GROQ_API_KEY` needed).

```bash
./run_lapa.sh
```

First run downloads the Lapa model (~4 GB). Subsequent runs start in seconds.

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | yes | BotFather token |
| `CONVEX_URL` | yes | Convex deployment URL |
| `QDRANT_URL` | yes | Qdrant Cloud endpoint |
| `QDRANT_API_KEY` | yes | Qdrant Cloud API key |
| `GROQ_API_KEY` | yes* | Groq API key (*required for Groq mode) |
| `GROQ_MODEL` | no | Groq model (default: `llama-3.3-70b-versatile`) |
| `DISABLE_GROQ` | no | Set to `1` to skip Groq and use Lapa only |
| `LLAMA_MODEL` | no | Ollama model name (default: lapa-v0.1.2-instruct-GGUF) |
| `OLLAMA_URL` | no | Ollama endpoint (default: `http://localhost:11434/api/generate`) |
| `DISABLE_LLAMA` | no | Set to `1` to skip Ollama/Lapa (Groq-only mode) |
| `CHUNK_THRESHOLD` | no | Min chunk similarity score (default: `0.80`) |
| `DEBUG_MODE` | no | Set to `1` for verbose per-message debug logs |

## Project structure

```
bot.py                  Telegram handlers, onboarding, user state
main.py                 Entrypoint (bot / ingest modes)
src/
  assistant.py          Orchestrates triage → retrieval → generation
  classifier.py         liberta encoder + Qdrant kb_qa search + keyword boost
  retriever.py          Qdrant client (kb_qa + kb_chunks), brand cascade
  generator.py          Groq API (primary) → Lapa via Ollama (fallback) → raw text
  triage.py             Rule-based shortcuts (greeting, safety, follow-ups)
  knowledge_base.py     Loads KB from Convex (fallback: JSON)
scripts/
  ingest_pdfs.py        PDF → chunks → Qdrant kb_chunks
  ingest_kb.py          KB entries → Qdrant kb_qa
  upload_vectors.py     Upload pre-encoded vectors to Qdrant Cloud
  seed_convex.py        knowledge_base.json → Convex
  start_ollama_tunnel.sh  Ollama + ngrok tunnel for Railway
data/
  knowledge_base.json   355 Q&A entries (137 curated + manual extracts, git-tracked)
  manuals/              PDF manuals by brand (gitignored, 1.6 GB)
docs/
  architecture.md       System diagram + design decisions
  ai-pipeline.md        Full pipeline flow + chunk cascade + triage rules
  data-pipeline.md      Ingest scripts + Qdrant Cloud setup
  knowledge-base.md     KB structure, categories, brand validation
```

## Docs

- [Architecture](docs/architecture.md)
- [AI Pipeline](docs/ai-pipeline.md)
- [Data Pipeline](docs/data-pipeline.md)
- [Knowledge Base](docs/knowledge-base.md)
