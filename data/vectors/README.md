# Vector Data — Transfer & Upload Instructions

## What is this?

Pre-encoded vector embeddings for CaffeBot knowledge base.
71,869 chunks parsed from 263 coffee machine PDF manuals (Ukrainian/Russian/English).
Encoded with `Goader/liberta-large` model (1024-dim, multilingual).

## Files

| File | Size | Description |
|------|------|-------------|
| `chunks.npy` | 281MB | Float32 array [71869, 1024] — all encoded vectors |
| `payloads.jsonl` | 98MB | Chunk metadata — one JSON per line (brand, model, text, page, file) |

---

## What to do on this machine

### 1. Make sure Qdrant is running

```bash
docker compose up -d qdrant
```

Check it's alive:
```bash
curl http://localhost:6333/healthz
```

### 2. Put these files in the project

Copy `chunks.npy` and `payloads.jsonl` into:
```
<project_root>/data/vectors/
```

### 3. Install dependencies

```bash
pip install qdrant-client numpy
```

### 4. Run upload

```bash
cd <project_root>
QDRANT_URL=http://localhost:6333 python3 scripts/upload_vectors.py
```

Upload takes ~5-15 min. Creates collection `kb_chunks` in Qdrant with all 71k vectors.

### 5. Verify

```bash
curl http://localhost:6333/collections/kb_chunks
```

Should show `"points_count": 71869`.

---

## Goal

These vectors power the CaffeBot Telegram bot semantic search.
When a user asks a question, the bot:
1. Encodes the query with the same model
2. Searches Qdrant for nearest chunks
3. Feeds relevant manual excerpts to LLM (Ollama) as context
4. Returns grounded answer about the specific coffee machine

No re-encoding needed — just upload and the bot works.
