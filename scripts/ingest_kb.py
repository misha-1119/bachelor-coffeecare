"""Ingest curated KB QA entries into Qdrant collection `kb_qa`.

- Loads the same KnowledgeBase the bot uses (Convex if CONVEX_URL set, else JSON).
- Skips auto-extracted manual entries via Classifier._is_manual_extract.
- Encodes with liberta-large (shared encoder; same instance used by VectorRetriever).
- Idempotent: collection is dropped and recreated each run.

Usage:
    python3 scripts/ingest_kb.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.classifier import _is_manual_extract  # noqa: E402
from src.knowledge_base import KnowledgeBase  # noqa: E402
from src.retriever import (  # noqa: E402
    QA_COLLECTION,
    VectorRetriever,
    load_default_encoder,
)


def build_embed_text(entry) -> str:
    kws = " ".join(entry.keywords[:10])
    return f"{entry.category}: {entry.question} | {kws}"


def main() -> int:
    print("[ingest_kb] loading KB...")
    kb = KnowledgeBase()
    total = len(kb.entries)
    items = []
    skipped = 0
    for e in kb.entries:
        if _is_manual_extract(e):
            skipped += 1
            continue
        items.append(
            {
                "entry_id": e.id,
                "category": e.category,
                "model": e.model,
                "question": e.question,
                "answer": e.answer,
                "keywords": list(e.keywords),
                "embed_text": build_embed_text(e),
            }
        )
    print(f"[ingest_kb] {total} total, {skipped} manual-extracts skipped, {len(items)} to upsert")

    print("[ingest_kb] loading encoder...")
    encoder = load_default_encoder()
    retriever = VectorRetriever(encoder)
    print(f"[ingest_kb] db_path={retriever.db_path} dim={retriever.vector_size}")
    retriever.reset_collection(QA_COLLECTION)

    t0 = time.time()
    count = retriever.upsert_qa(items)
    dt = time.time() - t0
    print(f"[ingest_kb] upserted {count} points in {dt:.1f}s")
    print(f"[ingest_kb] collection size: {retriever.count(QA_COLLECTION)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
