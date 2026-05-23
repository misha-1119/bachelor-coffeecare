"""Upload pre-encoded vectors from data/vectors/ into Qdrant kb_chunks collection.

Usage:
    QDRANT_URL=http://localhost:6333 python3 scripts/upload_vectors.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VECTORS_DIR = ROOT / "data" / "vectors"
CHUNKS_NPY = VECTORS_DIR / "chunks.npy"
PAYLOADS_JSONL = VECTORS_DIR / "payloads.jsonl"
BATCH_SIZE = 256


def main() -> int:
    from src.retriever import CHUNKS_COLLECTION, VectorRetriever, stable_id
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qm

    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    print(f"[upload] connecting to {qdrant_url}")
    client = QdrantClient(url=qdrant_url, timeout=120)
    client.get_collections()  # sanity check
    print("[upload] Qdrant OK")

    print(f"[upload] loading {CHUNKS_NPY.name} ...")
    vectors = np.load(str(CHUNKS_NPY))
    print(f"[upload] vectors shape: {vectors.shape}")

    print(f"[upload] loading {PAYLOADS_JSONL.name} ...")
    payloads = []
    with open(PAYLOADS_JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                payloads.append(json.loads(line))
    print(f"[upload] payloads: {len(payloads)}")

    if len(vectors) != len(payloads):
        print(f"[upload] ERROR: vectors {len(vectors)} != payloads {len(payloads)}")
        return 1

    vector_size = vectors.shape[1]
    print(f"[upload] vector_size={vector_size}")

    # Reset and recreate collection
    print(f"[upload] resetting {CHUNKS_COLLECTION}...")
    try:
        client.delete_collection(CHUNKS_COLLECTION)
    except Exception:
        pass
    client.create_collection(
        collection_name=CHUNKS_COLLECTION,
        vectors_config=qm.VectorParams(size=vector_size, distance=qm.Distance.COSINE),
    )
    client.create_payload_index(
        collection_name=CHUNKS_COLLECTION,
        field_name="model",
        field_schema=qm.PayloadSchemaType.KEYWORD,
    )
    client.create_payload_index(
        collection_name=CHUNKS_COLLECTION,
        field_name="brand",
        field_schema=qm.PayloadSchemaType.KEYWORD,
    )
    print("[upload] collection ready")

    # Upload in batches
    total = len(payloads)
    t0 = time.time()
    for start in range(0, total, BATCH_SIZE):
        batch_p = payloads[start:start + BATCH_SIZE]
        batch_v = vectors[start:start + BATCH_SIZE]
        points = [
            qm.PointStruct(
                id=stable_id(f"chunk:{p['chunk_id']}"),
                vector=batch_v[i].tolist(),
                payload=p,
            )
            for i, p in enumerate(batch_p)
        ]
        for attempt in range(5):
            try:
                client.upsert(collection_name=CHUNKS_COLLECTION, points=points)
                break
            except Exception as e:
                if attempt == 4:
                    raise
                wait = 2 ** attempt
                print(f"  [retry {attempt+1}/4] {e.__class__.__name__}: {e} — wait {wait}s")
                time.sleep(wait)
        done = start + len(batch_p)
        pct = done / total * 100
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate if rate > 0 else 0
        print(f"  {done}/{total} ({pct:.1f}%) | {rate:.0f} pts/s | ETA {eta:.0f}s")

    dt = time.time() - t0
    count = client.count(collection_name=CHUNKS_COLLECTION, exact=True).count
    print(f"[upload] done: {count} points in {dt:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
