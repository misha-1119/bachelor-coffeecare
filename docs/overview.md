# CaffeBot — Project Overview

## What is CaffeBot?

CaffeBot is a Ukrainian-language Telegram chatbot that helps users troubleshoot coffee machines. Users describe a problem in natural language — a symptom, error code, or question — and the bot responds with a concise, conversational solution in Ukrainian, citing the source manual when the answer comes from a PDF.

## Goals

- Answer common coffee machine questions without a human operator.
- Support error codes, cleaning cycles, brewing issues, maintenance, water system problems.
- Work across 81+ machine models from 21 brands (DeLonghi, Philips, Jura, Krups, Ardesto, …).
- Surface relevant pages from the manufacturer manuals when no curated answer exists.
- Respond in natural Ukrainian, not copy-pasted manual text.
- Run fully locally — no external LLM API costs.

## Core Idea

Three-stage pipeline:

1. **Triage** — fast rule-based shortcuts (greetings, safety, follow-ups).
2. **Semantic retrieval** (liberta-large Ukrainian BERT + Qdrant) — two vector collections:
   - `kb_qa` — 72 curated Q&A entries, primary path.
   - `kb_chunks` — thousands of PDF manual chunks, fallback path with a 3-tier brand cascade (exact model → same brand → universal).
3. **Conversational generation** (Lapa LLM via Ollama) — rewrites the retrieved instruction into a short, human-like reply, with a citation footer for manual hits.

The knowledge base (290 curated Q&A entries) lives in Convex cloud — loaded at startup, no local file dependency at runtime. The Qdrant vector index is a derived local cache, rebuilt with `scripts/ingest_kb.py` and `scripts/ingest_pdfs.py`.

## Language

All user-facing responses are in Ukrainian. The KB entries, keywords, and prompts are Ukrainian-first with multilingual keyword support (Ukrainian/Russian/English) for broader matching.
