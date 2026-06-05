"""
VectorRetriever: Qdrant-backed semantic search over curated KB QA entries and
PDF manual chunks. Shares a single SentenceTransformer (liberta-large by default)
across both collections so query embeddings cost one forward pass.
"""

from __future__ import annotations

import json
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

_MIN_CHUNK_LEN = 40   # chars — filters table-cell noise like "250г" or "milk"
_MIN_UA_RATIO = 0.30  # fraction of alpha chars that must be Cyrillic
_MIN_UA_SPECIFIC = 0.015  # fraction of alpha chars that must be uniquely Ukrainian (і, ї, є, ґ)
_UA_UNIQUE = frozenset("іїєґІЇЄҐ")  # letters absent from Russian/Macedonian/Bulgarian


def _ua_ratio(text: str) -> float:
    """Fraction of alphabetic chars that are Cyrillic (UA/RU)."""
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return 1.0
    return sum(1 for c in alpha if "Ѐ" <= c <= "ӿ") / len(alpha)


def _ua_specific_ratio(text: str) -> float:
    """Fraction of alphabetic chars that are uniquely Ukrainian (і, ї, є, ґ).
    Russian, Macedonian, Bulgarian don't use these letters → filters cross-language noise."""
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return 0.0
    return sum(1 for c in alpha if c in _UA_UNIQUE) / len(alpha)


def _is_coherent(text: str) -> bool:
    """Reject garbled PDF parse: too many short word-fragments (word endings / OCR noise).

    Corrupted example: 'сть . ві ати:. з молоком. дповідає кно тепер повер'
    In valid UA text, ≤ 35% of words are 1-3 chars; corrupted text routinely hits 50%+.
    """
    words = [w for w in re.split(r"[\s.,;:()\[\]]+", text) if w.isalpha()]
    if len(words) < 4:
        return True  # too few tokens to judge
    short = sum(1 for w in words if len(w) <= 3)
    return short / len(words) < 0.45


def stable_id(key: str) -> str:
    return str(uuid.uuid5(_UUID_NAMESPACE, key))


def _normalize_slug(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", "_", s)
    return s[:60]


_INDEX_PATH = Path(__file__).resolve().parent.parent / "data" / "manuals" / "index.json"


def _load_known_model_slugs() -> list[str]:
    """All real Qdrant model slugs from data/manuals/index.json (one per PDF)."""
    if not _INDEX_PATH.exists():
        return []
    try:
        items = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    slugs: list[str] = []
    seen: set[str] = set()
    for item in items:
        raw = item.get("slug", "")
        qslug = _normalize_slug(raw)
        if qslug and qslug not in seen:
            seen.add(qslug)
            slugs.append(qslug)
    return slugs


def _slug_tokens(slug: str) -> set[str]:
    """Tokenize slug by underscore. Keep all non-empty tokens, including short
    variant letters like 's', 'b' that distinguish models (Magnifica S vs Evo)."""
    return {t for t in slug.split("_") if t}


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
        self.vector_size = vector_size or _encoder_dim(encoder)

        qdrant_url = os.getenv("QDRANT_URL")
        if qdrant_url:
            self.db_path = None
            self.client = QdrantClient(
                url=qdrant_url,
                api_key=os.getenv("QDRANT_API_KEY"),
                timeout=120,
                prefer_grpc=False,
            )
        else:
            self.db_path = db_path or os.getenv("QDRANT_PATH", DEFAULT_DB_PATH)
            Path(self.db_path).mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=self.db_path)

        self._cached_encode = lru_cache(maxsize=512)(self._encode_raw)
        self._known_slugs: list[str] = _load_known_model_slugs()
        self._known_slug_tokens: list[tuple[str, set[str]]] = [
            (s, _slug_tokens(s)) for s in self._known_slugs
        ]
        if self._known_slugs:
            print(f"[retriever] known model slugs: {len(self._known_slugs)}")

    def _expand_model(self, model: str | None) -> list[str] | None:
        """Expand a user-stored model slug to all Qdrant slugs whose tokens
        cover every user token (exact match, or prefix match when user token is
        ≥3 chars — handles ingest's dash-stripping like 'ycm-d060'→'ycmd060').
        Short tokens like 's' require EXACT match so 'magnifica_s' stays
        distinct from 'magnifica_start'.
        """
        if not model or model == "universal":
            return None
        user_tokens = _slug_tokens(model)
        if not user_tokens:
            return [model]
        matches: list[str] = []
        for slug, tokens in self._known_slug_tokens:
            if self._tokens_cover(user_tokens, tokens):
                matches.append(slug)
        return matches or [model]

    @staticmethod
    def _tokens_cover(user_tokens: set[str], slug_tokens: set[str]) -> bool:
        for ut in user_tokens:
            if ut in slug_tokens:
                continue
            # Prefix match for alpha tokens ≥2 chars (handles series codes like
            # 'eq'→'eq300', 'ycm'→'ycmd060'). Digit tokens must match exactly
            # so '22' doesn't prefix-match '220'. Single letters never prefix.
            if len(ut) >= 2 and ut.isalpha() and any(st.startswith(ut) for st in slug_tokens):
                continue
            return False
        return True

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

        models_list = self._expand_model(model)
        if models_list is None:
            return None
        return qm.Filter(
            should=[
                qm.FieldCondition(key="model", match=qm.MatchAny(any=models_list)),
                qm.FieldCondition(key="model", match=qm.MatchValue(value="universal")),
            ]
        )

    def _brand_filter(
        self,
        brand: str,
        exclude_model: str | None = None,
        exclude_chunk_ids: list[str] | None = None,
    ):
        from qdrant_client.http import models as qm

        must = [qm.FieldCondition(key="brand", match=qm.MatchValue(value=brand))]
        must_not = []
        if exclude_model and exclude_model != "universal":
            excl = self._expand_model(exclude_model) or [exclude_model]
            must_not.append(qm.FieldCondition(key="model", match=qm.MatchAny(any=excl)))
        if exclude_chunk_ids:
            must_not.append(
                qm.FieldCondition(key="chunk_id", match=qm.MatchAny(any=list(exclude_chunk_ids)))
            )
        return qm.Filter(must=must, must_not=must_not or None)

    def _chunks_model_filter(
        self,
        model: str | None,
        exclude_chunk_ids: list[str] | None = None,
    ):
        from qdrant_client.http import models as qm

        should = None
        models_list = self._expand_model(model)
        if models_list:
            should = [
                qm.FieldCondition(key="model", match=qm.MatchAny(any=models_list)),
                qm.FieldCondition(key="model", match=qm.MatchValue(value="universal")),
            ]
        must_not = None
        if exclude_chunk_ids:
            must_not = [
                qm.FieldCondition(key="chunk_id", match=qm.MatchAny(any=list(exclude_chunk_ids)))
            ]
        if should is None and must_not is None:
            return None
        return qm.Filter(should=should, must_not=must_not)

    def _filter_chunks(self, hits: list[SearchHit], k: int) -> list[SearchHit]:
        """Remove short/non-UA/corrupted/duplicate chunks, return top-k survivors."""
        seen: set[str] = set()
        result: list[SearchHit] = []
        for h in hits:
            text = (h.payload.get("text") or "").strip()
            if len(text) < _MIN_CHUNK_LEN:
                continue
            if _ua_ratio(text) < _MIN_UA_RATIO:
                continue
            if _ua_specific_ratio(text) < _MIN_UA_SPECIFIC:
                continue
            if not _is_coherent(text):
                continue
            key = text[:120]
            if key in seen:
                continue
            seen.add(key)
            result.append(h)
            if len(result) == k:
                break
        return result

    def _search(self, collection: str, query: str, model: str | None, k: int) -> list[SearchHit]:
        vec = self.encode(query)
        result = self.client.query_points(
            collection_name=collection,
            query=vec.tolist(),
            query_filter=self._model_filter(model),
            limit=k,
            with_payload=True,
        )
        return [SearchHit(score=float(p.score), payload=dict(p.payload or {})) for p in result.points]

    def search_qa(self, query: str, model: str | None = None, k: int = 5) -> list[SearchHit]:
        return self._search(QA_COLLECTION, query, model, k)

    def search_chunks(
        self,
        query: str,
        model: str | None = None,
        k: int = 3,
        exclude_chunk_ids: list[str] | None = None,
    ) -> list[SearchHit]:
        vec = self.encode(query)
        result = self.client.query_points(
            collection_name=CHUNKS_COLLECTION,
            query=vec.tolist(),
            query_filter=self._chunks_model_filter(model, exclude_chunk_ids),
            limit=min(k * 8, 80),
            with_payload=True,
        )
        raw = [SearchHit(score=float(p.score), payload=dict(p.payload or {})) for p in result.points]
        return self._filter_chunks(raw, k)

    def search_chunks_by_brand(
        self,
        query: str,
        brand: str,
        k: int = 3,
        exclude_model: str | None = None,
        exclude_chunk_ids: list[str] | None = None,
    ) -> list[SearchHit]:
        vec = self.encode(query)
        result = self.client.query_points(
            collection_name=CHUNKS_COLLECTION,
            query=vec.tolist(),
            query_filter=self._brand_filter(
                brand, exclude_model=exclude_model, exclude_chunk_ids=exclude_chunk_ids
            ),
            limit=min(k * 8, 80),
            with_payload=True,
        )
        raw = [SearchHit(score=float(p.score), payload=dict(p.payload or {})) for p in result.points]
        return self._filter_chunks(raw, k)

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
