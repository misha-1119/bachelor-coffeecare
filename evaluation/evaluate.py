"""
Evaluation: compare NLP pipeline (liberta-large) vs rule-based baseline.

Metrics: precision, recall, F1-score (macro), top-1 accuracy, avg latency.
"""

import json
import time
from pathlib import Path

from sklearn.metrics import classification_report

from src.knowledge_base import KnowledgeBase
from src.classifier import Classifier
from src.rule_based import RuleBasedAssistant


TEST_PATH = Path(__file__).parent.parent / "data" / "test_queries.json"


def _load_queries() -> list[dict]:
    with open(TEST_PATH, encoding="utf-8") as f:
        return json.load(f)


def _eval_run(name: str, queries: list[dict], predict_fn) -> dict:
    y_true_cat, y_pred_cat = [], []
    correct_top1 = 0
    latencies = []

    for q in queries:
        t0 = time.time()
        pred_cat, pred_id = predict_fn(q["query"])
        latencies.append(time.time() - t0)

        y_true_cat.append(q["expected_category"])
        y_pred_cat.append(pred_cat or "unknown")

        if pred_id == q["expected_kb_id"]:
            correct_top1 += 1

    report = classification_report(
        y_true_cat,
        y_pred_cat,
        output_dict=True,
        zero_division=0,
    )

    macro = report["macro avg"]
    return {
        "name": name,
        "precision": macro["precision"],
        "recall": macro["recall"],
        "f1": macro["f1-score"],
        "top1_accuracy": correct_top1 / len(queries),
        "avg_latency_s": sum(latencies) / len(latencies),
        "report": report,
    }


def run_full_evaluation() -> dict:
    queries = _load_queries()
    kb = KnowledgeBase()
    classifier = Classifier(kb)
    rb = RuleBasedAssistant(kb)

    def nlp_predict(q: str):
        entry, _ = classifier.get_best_match(q)
        return (entry.category, entry.id) if entry else (None, None)

    def rb_predict(q: str):
        entry, _ = rb.get_best_match(q)
        return (entry.category, entry.id) if entry else (None, None)

    nlp_metrics = _eval_run("NLP (liberta-large)", queries, nlp_predict)
    rb_metrics = _eval_run("Rule-based (keywords)", queries, rb_predict)

    print("\n" + "=" * 70)
    print(f"{'Метрика':<22}{'NLP':>20}{'Rule-based':>20}")
    print("=" * 70)
    rows = [
        ("Precision (macro)", "precision"),
        ("Recall (macro)", "recall"),
        ("F1-score (macro)", "f1"),
        ("Top-1 accuracy", "top1_accuracy"),
        ("Avg latency (s)", "avg_latency_s"),
    ]
    for label, key in rows:
        print(f"{label:<22}{nlp_metrics[key]:>20.4f}{rb_metrics[key]:>20.4f}")
    print("=" * 70 + "\n")

    return {"nlp": nlp_metrics, "rule_based": rb_metrics}


if __name__ == "__main__":
    run_full_evaluation()
