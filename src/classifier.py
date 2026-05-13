"""
Stage 1: liberta-large (Ukrainian BERT) — semantic search and category classification.

When a `VectorRetriever` is supplied, semantic search is delegated to Qdrant
(persistent HNSW + payload filter on `model`). The legacy in-memory NumPy path
is kept as a fallback for tests / local debugging.
"""

import re

import numpy as np
from sentence_transformers import SentenceTransformer

from src.knowledge_base import KnowledgeBase, KBEntry

ERROR_CODE_RE = re.compile(r"\be\s*0?(\d{1,2})\b", re.IGNORECASE)

PRIMARY_MODEL = "Goader/liberta-large"
FALLBACK_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"


def _is_manual_extract(entry: KBEntry) -> bool:
    """Auto-extracted PDF manual entries are noisy — exclude from default search."""
    if "Інструкція для" in (entry.question or ""):
        return True
    if "Інструкція для" in (entry.answer or "")[:200]:
        return True
    eid = entry.id or ""
    for marker in ("_specs", "_settings", "_cleaning_uk", "_cleaning_en", "_brewing_001"):
        if marker in eid:
            return True
    return False


class Classifier:
    def __init__(self, kb: KnowledgeBase, retriever=None):
        self.kb = kb
        self.retriever = retriever
        self.entries_by_id = {e.id: e for e in kb.entries}
        if retriever is not None:
            self.model = retriever.encoder
            self.is_manual = None
            self.kb_embeddings = None
        else:
            self.is_manual = np.array([_is_manual_extract(e) for e in kb.entries], dtype=bool)
            self.model = self._load_model()
            self.kb_embeddings = self._encode_kb()

    def _load_model(self) -> SentenceTransformer:
        try:
            print(f"[Classifier] Loading {PRIMARY_MODEL}...")
            return SentenceTransformer(PRIMARY_MODEL, trust_remote_code=True)
        except Exception as exc:
            print(f"[Classifier] Primary model failed ({exc}). Falling back to {FALLBACK_MODEL}.")
            return SentenceTransformer(FALLBACK_MODEL)

    def _encode_kb(self) -> np.ndarray:
        texts = []
        for e in self.kb.entries:
            kws = " ".join(e.keywords[:10])
            texts.append(f"{e.category}: {e.question} | {kws}")
        emb = self.model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
        return emb

    def _model_mask(self, machine_model: str | None) -> np.ndarray:
        """Boolean mask: True for entries that apply to the user's machine."""
        if not machine_model or machine_model == "universal":
            return np.ones(len(self.kb.entries), dtype=bool)
        return np.array(
            [e.model == machine_model or e.model == "universal" for e in self.kb.entries],
            dtype=bool,
        )

    def _keyword_boost(self, query_lower: str, entry: KBEntry) -> float:
        """Lexical bonus combining full-phrase, full-word and stem (4-char) matches."""
        boost = 0.0
        for kw in entry.keywords:
            kw_l = kw.lower().strip()
            if not kw_l:
                continue
            if kw_l in query_lower:
                boost += 0.15
                if len(kw_l) >= 4:
                    boost += 0.05
                continue
            for word in kw_l.split():
                if len(word) >= 4 and word[:4] in query_lower:
                    boost += 0.04
        return min(boost, 0.45)

    def _error_code_override(self, query: str, machine_model: str | None = None) -> KBEntry | None:
        """If the query mentions E01-E20 etc., always return the matching KB entry.
        Prefers a model-specific entry over a universal one when available."""
        m = ERROR_CODE_RE.search(query)
        if not m:
            return None
        code_num = m.group(1).zfill(2)
        target = f"e{code_num}"
        candidates: list[KBEntry] = []
        for e in self.kb.entries:
            if e.category != "error_code":
                continue
            for kw in e.keywords:
                kw_clean = kw.lower().replace(" ", "")
                if kw_clean == target or kw_clean == f"err{code_num}" or kw_clean == f"error{code_num}":
                    candidates.append(e)
                    break
        if not candidates:
            return None
        if machine_model and machine_model != "universal":
            for c in candidates:
                if c.model == machine_model:
                    return c
        for c in candidates:
            if c.model == "universal":
                return c
        return candidates[0]

    def get_best_match(
        self,
        query: str,
        machine_model: str | None = None,
        exclude_ids: set[str] | None = None,
    ) -> tuple[KBEntry | None, float]:
        if not self.kb.entries:
            return None, 0.0

        forced = self._error_code_override(query, machine_model)
        if forced is not None and (not exclude_ids or forced.id not in exclude_ids):
            return forced, 1.0

        if self.retriever is not None:
            return self._best_match_vector(query, machine_model, exclude_ids)
        return self._best_match_numpy(query, machine_model, exclude_ids)

    def _best_match_vector(
        self,
        query: str,
        machine_model: str | None,
        exclude_ids: set[str] | None,
    ) -> tuple[KBEntry | None, float]:
        # pool=50: keyword boost can lift gold answers ranked 11+ by raw cosine
        # (validated by evaluation/eval_retrieval.py — recall jumps when pool < 50)
        try:
            hits = self.retriever.search_qa(query, machine_model, k=50)
        except Exception as exc:
            print(f"[Classifier] retriever.search_qa failed ({exc}); reverting to numpy path")
            self.retriever = None
            if self.kb_embeddings is None:
                self.is_manual = np.array([_is_manual_extract(e) for e in self.kb.entries], dtype=bool)
                self.kb_embeddings = self._encode_kb()
            return self._best_match_numpy(query, machine_model, exclude_ids)
        if not hits:
            return None, 0.0
        q_lower = query.lower()
        best_entry: KBEntry | None = None
        best_score = -1.0
        for hit in hits:
            entry_id = hit.payload.get("entry_id")
            if not entry_id:
                continue
            if exclude_ids and entry_id in exclude_ids:
                continue
            entry = self.entries_by_id.get(entry_id)
            if entry is None:
                continue
            score = hit.score + self._keyword_boost(q_lower, entry)
            if score > best_score:
                best_score = score
                best_entry = entry
        if best_entry is None:
            return None, 0.0
        return best_entry, float(best_score)

    def _best_match_numpy(
        self,
        query: str,
        machine_model: str | None,
        exclude_ids: set[str] | None,
    ) -> tuple[KBEntry | None, float]:
        q_emb = self.model.encode([query], convert_to_numpy=True, normalize_embeddings=True)[0]
        sem_scores = self.kb_embeddings @ q_emb
        q_lower = query.lower()
        boosts = np.array([self._keyword_boost(q_lower, e) for e in self.kb.entries])
        mask = self._model_mask(machine_model)
        scores = sem_scores + boosts
        scores = np.where(mask, scores, -1.0)
        # Heavily penalize auto-extracted PDF manual entries — they are noisy
        # and grab unrelated short queries via spurious embedding similarity.
        scores = np.where(self.is_manual, scores - 0.5, scores)
        if exclude_ids:
            for i, e in enumerate(self.kb.entries):
                if e.id in exclude_ids:
                    scores[i] = -1.0
        idx = int(np.argmax(scores))
        return self.kb.entries[idx], float(scores[idx])

    def classify_category(self, query: str) -> str:
        entry, _ = self.get_best_match(query)
        return entry.category if entry else "general"
