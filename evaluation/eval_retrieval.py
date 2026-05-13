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


def _topk_qdrant(retriever: VectorRetriever, query: str, k: int) -> list[str]:
    from src.triage import normalize as _normalize

    hits = retriever.search_qa(_normalize(query), machine_model=None, k=k)
    return [h.payload.get("entry_id", "") for h in hits]


def _topk_qdrant_chunks(retriever: VectorRetriever, query: str, k: int) -> list[str]:
    """QA top-k, but if best QA score is weak, splice top chunk page ref in slot 0."""
    from src.triage import normalize as _normalize

    qn = _normalize(query)
    qa = retriever.search_qa(qn, machine_model=None, k=k)
    qa_ids = [h.payload.get("entry_id", "") for h in qa]
    best_score = qa[0].score if qa else 0.0
    if best_score < 0.55:
        chunks = retriever.search_chunks(qn, machine_model=None, k=1)
        if chunks and chunks[0].score >= 0.45:
            # surface a synthetic identifier so the test sees the manual reference
            tag = f"manual:{chunks[0].payload.get('file', '')}#{chunks[0].payload.get('page_start', 0)}"
            qa_ids = [tag] + qa_ids
    return qa_ids[:k]


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

    results = []

    if "numpy" in args.variants:
        print("[eval] preparing numpy classifier (encoding full KB)...")
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
        results.append(_run_variant("qdrant", queries, lambda q, k: _topk_qdrant(retriever, q, k)))

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
                    "qdrant+chunks", queries, lambda q, k: _topk_qdrant_chunks(retriever, q, k)
                )
            )

    _print_md_table(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
