# CaffeBot — Project Overview

## What is CaffeBot?

CaffeBot is a Ukrainian-language Telegram chatbot that helps users troubleshoot coffee machines. Users describe a problem in natural language — a symptom, error code, or question — and the bot responds with a concise, conversational solution in Ukrainian.

## Goals

- Answer common coffee machine questions without a human operator
- Support error codes, cleaning cycles, brewing issues, maintenance, water system problems
- Work across 81+ machine models (DeLonghi, Philips, Jura, Krups, Ardesto, etc.)
- Respond in natural Ukrainian, not copy-pasted manual text
- Run fully locally — no external LLM API costs

## Core Idea

Instead of a simple FAQ bot, CaffeBot uses a two-stage AI pipeline:

1. **Semantic search** (liberta-large, Ukrainian BERT) — understands the *meaning* of the user's question
2. **Conversational generation** (Lapa LLM via Ollama) — rewrites the KB answer into a short, human-like reply

The knowledge base (290 curated Q&A entries) lives in Convex cloud — loaded at startup, no local file dependency at runtime.

## Language

All user-facing responses are in Ukrainian. The KB entries, keywords, and prompts are Ukrainian-first with multilingual keyword support (Ukrainian/Russian/English) for broader matching.
