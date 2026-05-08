"""
Baseline: rule-based keyword matching assistant.

Used to compare against the NLP pipeline in evaluation.
"""

from src.knowledge_base import KnowledgeBase, KBEntry


class RuleBasedAssistant:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def _score(self, query: str, entry: KBEntry) -> float:
        q = query.lower()
        score = 0.0
        for kw in entry.keywords:
            kw_lower = kw.lower()
            if kw_lower in q:
                score += 2.0
            else:
                for token in kw_lower.split():
                    if token in q:
                        score += 1.0
        return score / max(1, len(entry.keywords))

    def get_best_match(self, query: str) -> tuple[KBEntry | None, float]:
        if not self.kb.entries:
            return None, 0.0
        scored = [(e, self._score(query, e)) for e in self.kb.entries]
        scored.sort(key=lambda x: x[1], reverse=True)
        best, best_score = scored[0]
        if best_score == 0:
            return None, 0.0
        return best, best_score

    def respond(self, query: str) -> dict:
        entry, score = self.get_best_match(query)
        if entry is None:
            return {
                "response": "Не вдалося знайти відповідь за ключовими словами. Спробуйте переформулювати.",
                "category": "unknown",
                "kb_entry_id": None,
                "confidence": 0.0,
                "source": "rule_based_no_match",
            }
        return {
            "response": entry.answer,
            "category": entry.category,
            "kb_entry_id": entry.id,
            "confidence": score,
            "source": "rule_based",
        }
