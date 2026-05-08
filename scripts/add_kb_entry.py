"""Interactive helper to add a new KB entry to data/knowledge_base.json
and push it to Convex.

Usage:
    python3 scripts/add_kb_entry.py

Walks you through fields step by step. Auto-assigns next kb_NNN id.
"""

import json
import os
import re
import sys
from pathlib import Path

from convex import ConvexClient
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

KB_PATH = ROOT / "data" / "knowledge_base.json"
CONVEX_URL = os.getenv("CONVEX_URL")


def _next_numeric_id(entries: list[dict]) -> str:
    nums = []
    for e in entries:
        m = re.match(r"^kb_(\d+)$", e["id"])
        if m:
            nums.append(int(m.group(1)))
    nxt = (max(nums) + 1) if nums else 1
    return f"kb_{nxt:03d}"


def _multiline_input(prompt: str) -> str:
    print(f"{prompt} (порожній рядок — кінець):")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            if lines and lines[-1] == "":
                lines.pop()
                break
            lines.append("")
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _list_input(prompt: str) -> list[str]:
    raw = input(f"{prompt} (через кому): ").strip()
    return [s.strip() for s in raw.split(",") if s.strip()]


def main():
    with open(KB_PATH, encoding="utf-8") as f:
        data = json.load(f)

    categories = data["categories"]

    print("\n=== Додавання нової KB-entry ===\n")
    print(f"Доступні категорії: {', '.join(categories)}")
    print(f"Поточна кількість entries: {len(data['entries'])}\n")

    custom_id = input("ID (Enter — авто): ").strip()
    new_id = custom_id if custom_id else _next_numeric_id(data["entries"])
    if any(e["id"] == new_id for e in data["entries"]):
        print(f"ERROR: id '{new_id}' уже існує.")
        sys.exit(1)

    category = input(f"Категорія [{categories[0]}]: ").strip() or categories[0]
    if category not in categories:
        ans = input(f"'{category}' нема у списку. Додати нову категорію? [y/N]: ").strip().lower()
        if ans == "y":
            categories.append(category)
        else:
            sys.exit(1)

    question = input("Питання (одне речення): ").strip()
    if not question:
        print("ERROR: питання обов'язкове.")
        sys.exit(1)

    keywords = _list_input("Ключові слова")
    if not keywords:
        print("ERROR: хоча б одне ключове слово потрібне.")
        sys.exit(1)

    answer = _multiline_input("Відповідь (можна багаторядково; два Enter — кінець)")
    if not answer:
        print("ERROR: відповідь обов'язкова.")
        sys.exit(1)

    model = input("Модель кавомашини [universal]: ").strip() or "universal"

    new_entry = {
        "id": new_id,
        "category": category,
        "keywords": keywords,
        "question": question,
        "answer": answer,
        "model": model,
    }

    print("\n=== Перевірте ===")
    print(json.dumps(new_entry, ensure_ascii=False, indent=2))
    confirm = input("\nЗберегти у JSON та Convex? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Скасовано.")
        sys.exit(0)

    data["entries"].append(new_entry)
    data["categories"] = categories
    with open(KB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✓ Додано до {KB_PATH}")

    if CONVEX_URL:
        client = ConvexClient(CONVEX_URL)
        client.mutation("kb:upsertCategory", {"name": category})
        client.mutation(
            "kb:upsertEntry",
            {
                "entryId": new_entry["id"],
                "category": new_entry["category"],
                "keywords": new_entry["keywords"],
                "question": new_entry["question"],
                "answer": new_entry["answer"],
                "model": new_entry["model"],
            },
        )
        print("✓ Залито у Convex")
    else:
        print("CONVEX_URL не виставлено — пропускаю Convex (тільки JSON оновлено).")


if __name__ == "__main__":
    main()
