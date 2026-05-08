import json
import os
from pathlib import Path
from dataclasses import dataclass


@dataclass
class KBEntry:
    id: str
    category: str
    keywords: list[str]
    question: str
    answer: str
    model: str


class KnowledgeBase:
    """Loads KB from Convex if CONVEX_URL is set, otherwise from local JSON."""

    def __init__(self, path: str = None, source: str = None):
        if path is None:
            path = Path(__file__).parent.parent / "data" / "knowledge_base.json"
        self.path = Path(path)
        self.entries: list[KBEntry] = []
        self.categories: list[str] = []
        self.source = source or ("convex" if os.getenv("CONVEX_URL") else "json")
        self._load()

    def _load(self):
        if self.source == "convex":
            try:
                self._load_from_convex()
                return
            except Exception as exc:
                print(f"[KnowledgeBase] Convex load failed ({exc}). Falling back to JSON.")
                self.source = "json"
        self._load_from_json()

    def _load_from_json(self):
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self.categories = data["categories"]
        self.entries = [
            KBEntry(
                id=e["id"],
                category=e["category"],
                keywords=e["keywords"],
                question=e["question"],
                answer=e["answer"],
                model=e["model"],
            )
            for e in data["entries"]
        ]

    def _load_from_convex(self):
        from convex import ConvexClient

        url = os.getenv("CONVEX_URL")
        if not url:
            raise RuntimeError("CONVEX_URL not set")
        client = ConvexClient(url)
        cats = client.query("kb:listCategories", {})
        rows = client.query("kb:listEntries", {})
        if not rows:
            raise RuntimeError("Convex returned no KB entries (run scripts/seed_convex.py)")
        self.categories = list(cats)
        self.entries = [
            KBEntry(
                id=r["entryId"],
                category=r["category"],
                keywords=list(r["keywords"]),
                question=r["question"],
                answer=r["answer"],
                model=r["model"],
            )
            for r in rows
        ]

    def get_all_questions(self) -> list[str]:
        return [e.question for e in self.entries]

    def get_all_answers(self) -> list[str]:
        return [e.answer for e in self.entries]

    def get_by_id(self, entry_id: str) -> KBEntry | None:
        return next((e for e in self.entries if e.id == entry_id), None)

    def get_by_category(self, category: str) -> list[KBEntry]:
        return [e for e in self.entries if e.category == category]

    def get_for_model(self, model: str | None) -> list[KBEntry]:
        """Return entries for a specific machine model + universal entries."""
        if not model or model == "universal":
            return list(self.entries)
        return [e for e in self.entries if e.model == model or e.model == "universal"]

    def list_models(self) -> list[str]:
        return sorted({e.model for e in self.entries})

    def __len__(self):
        return len(self.entries)
