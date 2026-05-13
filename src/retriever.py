"""
VectorRetriever: Qdrant-backed semantic search over curated KB QA entries and
PDF manual chunks. Shares a single SentenceTransformer (liberta-large by default)
across both collections so query embeddings cost one forward pass.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

QA_COLLECTION = "kb_qa"
CHUNKS_COLLECTION = "kb_chunks"
VECTOR_SIZE = 1024
DEFAULT_DB_PATH = "./data/qdrant"
_UUID_NAMESPACE = uuid.UUID("c0ffee00-0000-0000-0000-000000000001")


def stable_id(key: str) -> str:
    return str(uuid.uuid5(_UUID_NAMESPACE, key))


@dataclass
class SearchHit:
    score: float
    payload: dict


def _encoder_dim(encoder) -> int:
    try:
        dim = int(encoder.get_sentence_embedding_dimension())
        if dim > 0:
            return dim
    except Exception:
        pass
    return VECTOR_SIZE


class VectorRetriever:
    def __init__(self, encoder, db_path: str | None = None, vector_size: int | None = None):
        from qdrant_client import QdrantClient

        self.encoder = encoder
        self.db_path = db_path or os.getenv("QDRANT_PATH", DEFAULT_DB_PATH)
        Path(self.db_path).mkdir(parents=True, exist_ok=True)
        self.vector_size = vector_size or _encoder_dim(encoder)
        self.client = QdrantClient(path=self.db_path)
        self._cached_encode = lru_cache(maxsize=512)(self._encode_raw)

    def _encode_raw(self, text: str) -> tuple[float, ...]:
        vec = self.encoder.encode([text], convert_to_numpy=True, normalize_embeddings=True)[0]
        return tuple(float(x) for x in vec)

    def encode(self, text: str) -> np.ndarray:
        norm = re.sub(r"\s+", " ", text.strip().lower())
        return np.array(self._cached_encode(norm), dtype=np.float32)

    def ensure_collections(self) -> None:
        from qdrant_client.http import models as qm

        existing = {c.name for c in self.client.get_collections().collections}
        for name in (QA_COLLECTION, CHUNKS_COLLECTION):
            if name not in existing:
                self.client.create_collection(
                    collection_name=name,
                    vectors_config=qm.VectorParams(size=self.vector_size, distance=qm.Distance.COSINE),
                )
                self.client.create_payload_index(
                    collection_name=name,
                    field_name="model",
                    field_schema=qm.PayloadSchemaType.KEYWORD,
                )

    def reset_collection(self, name: str) -> None:
        from qdrant_client.http import models as qm

        try:
            self.client.delete_collection(name)
        except Exception:
            pass
        self.client.create_collection(
            collection_name=name,
            vectors_config=qm.VectorParams(size=self.vector_size, distance=qm.Distance.COSINE),
        )
        self.client.create_payload_index(
            collection_name=name,
            field_name="model",
            field_schema=qm.PayloadSchemaType.KEYWORD,
        )

    def _encode_batch(self, texts: list[str]) -> np.ndarray:
        return self.encoder.encode(
            texts, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False
        )

    def upsert_qa(self, items: list[dict], batch_size: int = 64) -> int:
        """items: [{entry_id, category, model, question, answer, keywords, embed_text}]"""
        from qdrant_client.http import models as qm

        total = 0
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            vecs = self._encode_batch([it["embed_text"] for it in batch])
            points = [
                qm.PointStruct(
                    id=stable_id(f"qa:{it['entry_id']}"),
                    vector=vecs[i].tolist(),
                    payload={
                        "entry_id": it["entry_id"],
                        "category": it["category"],
                        "model": it.get("model") or "universal",
                        "question": it["question"],
                        "answer": it["answer"],
                        "keywords": it.get("keywords", []),
                    },
                )
                for i, it in enumerate(batch)
            ]
            self.client.upsert(collection_name=QA_COLLECTION, points=points)
            total += len(points)
        return total

    def upsert_chunks(self, items: list[dict], batch_size: int = 64) -> int:
        """items: [{chunk_id, brand, model, file, page_start, page_end, section, text}]"""
        from qdrant_client.http import models as qm

        total = 0
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            vecs = self._encode_batch([it["text"] for it in batch])
            points = [
                qm.PointStruct(
                    id=stable_id(f"chunk:{it['chunk_id']}"),
                    vector=vecs[i].tolist(),
                    payload={
                        "chunk_id": it["chunk_id"],
                        "brand": it.get("brand", ""),
                        "model": it.get("model") or "universal",
                        "file": it.get("file", ""),
                        "page_start": it.get("page_start", 0),
                        "page_end": it.get("page_end", 0),
                        "section": it.get("section", ""),
                        "text": it["text"],
                    },
                )
                for i, it in enumerate(batch)
            ]
            self.client.upsert(collection_name=CHUNKS_COLLECTION, points=points)
            total += len(points)
        return total

    def _model_filter(self, model: str | None):
        from qdrant_client.http import models as qm

        if not model or model == "universal":
            return None
        return qm.Filter(
            should=[
                qm.FieldCondition(key="model", match=qm.MatchValue(value=model)),
                qm.FieldCondition(key="model", match=qm.MatchValue(value="universal")),
            ]
        )

    def _brand_filter(self, brand: str, exclude_model: str | None = None):
        from qdrant_client.http import models as qm

        must = [qm.FieldCondition(key="brand", match=qm.MatchValue(value=brand))]
        must_not = None
        if exclude_model and exclude_model != "universal":
            must_not = [qm.FieldCondition(key="model", match=qm.MatchValue(value=exclude_model))]
        return qm.Filter(must=must, must_not=must_not)

    def _search(self, collection: str, query: str, model: str | None, k: int) -> list[SearchHit]:
        vec = self.encode(query)
        result = self.client.search(
            collection_name=collection,
            query_vector=vec.tolist(),
            query_filter=self._model_filter(model),
            limit=k,
            with_payload=True,
        )
        return [SearchHit(score=float(p.score), payload=dict(p.payload or {})) for p in result]

    def search_qa(self, query: str, model: str | None = None, k: int = 5) -> list[SearchHit]:
        return self._search(QA_COLLECTION, query, model, k)

    def search_chunks(self, query: str, model: str | None = None, k: int = 3) -> list[SearchHit]:
        return self._search(CHUNKS_COLLECTION, query, model, k)

    def search_chunks_by_brand(
        self,
        query: str,
        brand: str,
        k: int = 3,
        exclude_model: str | None = None,
    ) -> list[SearchHit]:
        vec = self.encode(query)
        result = self.client.search(
            collection_name=CHUNKS_COLLECTION,
            query_vector=vec.tolist(),
            query_filter=self._brand_filter(brand, exclude_model=exclude_model),
            limit=k,
            with_payload=True,
        )
        return [SearchHit(score=float(p.score), payload=dict(p.payload or {})) for p in result]

    def list_brands(self) -> list[str]:
        """Distinct `brand` values in kb_chunks. Cheap scroll over payload."""
        from qdrant_client.http import models as qm

        brands: set[str] = set()
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=CHUNKS_COLLECTION,
                limit=512,
                with_payload=qm.PayloadSelectorInclude(include=["brand"]),
                with_vectors=False,
                offset=offset,
            )
            for p in points:
                b = (p.payload or {}).get("brand")
                if b:
                    brands.add(b)
            if offset is None:
                break
        return sorted(brands)

    def count(self, collection: str) -> int:
        return int(self.client.count(collection_name=collection, exact=True).count)


def load_default_encoder():
    """Load the same SentenceTransformer the classifier uses, with the same fallback.

    Forces CPU by default to avoid MPS OOM contention with Ollama / other GPU users.
    Override with ENCODER_DEVICE=mps|cuda|cpu.
    """
    from sentence_transformers import SentenceTransformer

    from src.classifier import PRIMARY_MODEL, FALLBACK_MODEL

    device = os.getenv("ENCODER_DEVICE", "cpu")
    try:
        return SentenceTransformer(PRIMARY_MODEL, trust_remote_code=True, device=device)
    except Exception as exc:
        print(f"[VectorRetriever] Primary model failed ({exc}). Falling back to {FALLBACK_MODEL}.")
        return SentenceTransformer(FALLBACK_MODEL, device=device)


def build_default_retriever(encoder=None) -> VectorRetriever:
    enc = encoder or load_default_encoder()
    retriever = VectorRetriever(enc)
    retriever.ensure_collections()
    return retriever
