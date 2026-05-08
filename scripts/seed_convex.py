"""Seed Convex with KB data from data/knowledge_base.json.

Usage:
    1. Run `npx convex dev` once to deploy schema and get CONVEX_URL.
    2. Put CONVEX_URL in .env.
    3. python3 scripts/seed_convex.py
"""

import json
import os
import sys
from pathlib import Path

from convex import ConvexClient
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

CONVEX_URL = os.getenv("CONVEX_URL")
if not CONVEX_URL:
    print("ERROR: CONVEX_URL missing. Run `npx convex dev` first, then set CONVEX_URL in .env.")
    sys.exit(1)

KB_PATH = ROOT / "data" / "knowledge_base.json"


def main():
    client = ConvexClient(CONVEX_URL)

    with open(KB_PATH, encoding="utf-8") as f:
        data = json.load(f)

    print(f"Clearing existing KB in Convex...")
    client.mutation("kb:clearAll", {})

    print(f"Seeding {len(data['categories'])} categories...")
    for cat in data["categories"]:
        client.mutation("kb:upsertCategory", {"name": cat})

    print(f"Seeding {len(data['entries'])} entries...")
    for e in data["entries"]:
        client.mutation(
            "kb:upsertEntry",
            {
                "entryId": e["id"],
                "category": e["category"],
                "keywords": e["keywords"],
                "question": e["question"],
                "answer": e["answer"],
                "model": e["model"],
            },
        )

    entries = client.query("kb:listEntries", {})
    print(f"Done. Convex now has {len(entries)} entries.")


if __name__ == "__main__":
    main()
