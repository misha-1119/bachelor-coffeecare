"""Retrieval benchmark: numpy KB vs Qdrant kb_qa vs Qdrant kb_qa + chunk fallback.

Runs every query from data/test_queries.json against each retriever variant.
Reports Recall@1, Recall@5, MRR, top-1 accuracy on expected_kb_id, and latency
percentiles. Markdown summary table is printed at the end — paste straight into
the thesis evaluation chapter.

Usage:
    python3 evaluation/eval_retrieval.py
    python3 evaluation/eval_retrieval.py --variants numpy qdrant qdrant_chunks
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.classifier import Classifier  # noqa: E402
from src.knowledge_base import KnowledgeBase  # noqa: E402
from src.retriever import (  # noqa: E402
    CHUNKS_COLLECTION,
    QA_COLLECTION,
    VectorRetriever,
    load_default_encoder,
)

TEST_PATH = ROOT / "data" / "test_queries.json"


def _topk_numpy(classifier: Classifier, query: str, k: int) -> list[str]:
    import numpy as np

    from src.triage import normalize as _normalize

    q = _normalize(query)
    q_emb = classifier.model.encode([q], convert_to_numpy=True, normalize_embeddings=True)[0]
    scores = classifier.kb_embeddings @ q_emb
    q_lower = q.lower()
    boosts = np.array(
        [classifier._keyword_boost(q_lower, e) for e in classifier.kb.entries]
    )
    scores = scores + boosts
    scores = np.where(classifier.is_manual, scores - 0.5, scores)
    order = np.argsort(-scores)[:k]
    return [classifier.kb.entries[int(i)].id for i in order]


def _rerank_with_boost(hits, query: str, entries_by_id, classifier: Classifier, k: int) -> list[tuple[str, float]]:
    """Mirror production: cosine score + keyword boost, then sort. Returns [(entry_id, score), ...]."""
    q_lower = query.lower()
    scored: list[tuple[str, float]] = []
    for h in hits:
        eid = h.payload.get("entry_id", "")
        entry = entries_by_id.get(eid)
        if entry is None:
            scored.append((eid, float(h.score)))
            continue
        boost = classifier._keyword_boost(q_lower, entry)
        scored.append((eid, float(h.score) + boost))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


def _topk_qdrant(
    retriever: VectorRetriever,
    query: str,
    k: int,
    entries_by_id: dict,
    classifier: Classifier,
    pool: int = 50,
) -> list[str]:
    """Production-faithful: fetch top-`pool`, apply keyword boost, re-rank, return top-k."""
    from src.triage import normalize as _normalize

    qn = _normalize(query)
    hits = retriever.search_qa(qn, model=None, k=pool)
    reranked = _rerank_with_boost(hits, qn, entries_by_id, classifier, k)
    return [eid for eid, _ in reranked]


def _topk_qdrant_chunks(
    retriever: VectorRetriever,
    query: str,
    k: int,
    entries_by_id: dict,
    classifier: Classifier,
    pool: int = 50,
) -> list[str]:
    """QA top-k with boost; if best QA combined score is weak, splice top chunk page ref in slot 0."""
    from src.triage import normalize as _normalize

    qn = _normalize(query)
    hits = retriever.search_qa(qn, model=None, k=pool)
    reranked = _rerank_with_boost(hits, qn, entries_by_id, classifier, k)
    ids = [eid for eid, _ in reranked]
    best_combined = reranked[0][1] if reranked else 0.0
    if best_combined < 0.55:
        chunks = retriever.search_chunks(qn, model=None, k=1)
        if chunks and chunks[0].score >= 0.45:
            tag = f"manual:{chunks[0].payload.get('file', '')}#{chunks[0].payload.get('page_start', 0)}"
            ids = [tag] + ids
    return ids[:k]


def _metrics(ranked_ids: list[str], gold: str) -> tuple[float, float, float]:
    """Returns (recall@1, recall@5, reciprocal_rank)."""
    r1 = 1.0 if ranked_ids and ranked_ids[0] == gold else 0.0
    r5 = 1.0 if gold in ranked_ids[:5] else 0.0
    rr = 0.0
    for rank, eid in enumerate(ranked_ids, start=1):
        if eid == gold:
            rr = 1.0 / rank
            break
    return r1, r5, rr


def _summarize(name: str, rows: list[dict]) -> dict:
    if not rows:
        return {"name": name}
    lats = sorted(r["latency_ms"] for r in rows)

    def _p(q):
        idx = max(0, min(len(lats) - 1, int(q * (len(lats) - 1))))
        return lats[idx]

    return {
        "name": name,
        "n": len(rows),
        "recall@1": statistics.mean(r["r1"] for r in rows),
        "recall@5": statistics.mean(r["r5"] for r in rows),
        "mrr": statistics.mean(r["rr"] for r in rows),
        "p50_ms": _p(0.50),
        "p95_ms": _p(0.95),
        "mean_ms": statistics.mean(lats),
    }


def _run_variant(name: str, queries: list[dict], predict_topk) -> dict:
    rows = []
    for q in queries:
        gold = q["expected_kb_id"]
        t0 = time.perf_counter()
        ranked = predict_topk(q["query"], 5)
        dt = (time.perf_counter() - t0) * 1000.0
        r1, r5, rr = _metrics(ranked, gold)
        rows.append({"id": q["id"], "r1": r1, "r5": r5, "rr": rr, "latency_ms": dt})
    return _summarize(name, rows)


def _print_md_table(results: list[dict]) -> None:
    print()
    print("| variant | n | Recall@1 | Recall@5 | MRR | p50 ms | p95 ms | mean ms |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in results:
        print(
            f"| {r['name']} | {r['n']} | {r['recall@1']:.3f} | {r['recall@5']:.3f} | "
            f"{r['mrr']:.3f} | {r['p50_ms']:.1f} | {r['p95_ms']:.1f} | {r['mean_ms']:.1f} |"
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--variants",
        nargs="+",
        choices=["numpy", "qdrant", "qdrant_chunks"],
        default=["numpy", "qdrant", "qdrant_chunks"],
    )
    args = ap.parse_args()

    with open(TEST_PATH, encoding="utf-8") as f:
        queries = json.load(f)
    print(f"[eval] {len(queries)} test queries from {TEST_PATH.name}")

    encoder = load_default_encoder()
    kb = KnowledgeBase()
    entries_by_id = {e.id: e for e in kb.entries}

    results = []

    classifier_np = Classifier(kb)
    # The above re-loads liberta — replace its encoder with the already-loaded one
    classifier_np.model = encoder
    import numpy as np

    texts = [
        f"{e.category}: {e.question} | {' '.join(e.keywords[:10])}" for e in kb.entries
    ]
    classifier_np.kb_embeddings = encoder.encode(
        texts, convert_to_numpy=True, normalize_embeddings=True
    )

    if "numpy" in args.variants:
        print("[eval] running numpy variant...")
        results.append(
            _run_variant("numpy", queries, lambda q, k: _topk_numpy(classifier_np, q, k))
        )

    retriever = None
    if {"qdrant", "qdrant_chunks"} & set(args.variants):
        retriever = VectorRetriever(encoder)
        try:
            qa_count = retriever.count(QA_COLLECTION)
        except Exception:
            qa_count = 0
        print(f"[eval] kb_qa size: {qa_count}")
        if qa_count == 0:
            print("[eval] kb_qa empty — run scripts/ingest_kb.py first")
            return 1

    if "qdrant" in args.variants:
        results.append(
            _run_variant(
                "qdrant",
                queries,
                lambda q, k: _topk_qdrant(retriever, q, k, entries_by_id, classifier_np),
            )
        )

    if "qdrant_chunks" in args.variants:
        try:
            chunk_count = retriever.count(CHUNKS_COLLECTION)
        except Exception:
            chunk_count = 0
        print(f"[eval] kb_chunks size: {chunk_count}")
        if chunk_count == 0:
            print("[eval] kb_chunks empty — run scripts/ingest_pdfs.py first (skipping variant)")
        else:
            results.append(
                _run_variant(
                    "qdrant+chunks",
                    queries,
                    lambda q, k: _topk_qdrant_chunks(retriever, q, k, entries_by_id, classifier_np),
                )
            )

    _print_md_table(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
