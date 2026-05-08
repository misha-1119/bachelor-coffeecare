"""Detect and merge near-duplicate KB entries.

Strategy:
- Group by (category, label-prefix-of-id).
- Within group, hash answer text (normalized: lowercase, whitespace-collapsed,
  first 800 chars).
- If multiple entries share a hash but different models, drop all but one and
  re-tag survivor as model='universal'.
- If multiple entries share a hash AND the same model (rare), drop duplicates.

Usage:
    python3 scripts/dedupe_kb.py --dry-run
    python3 scripts/dedupe_kb.py            # rewrite JSON + push to Convex
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

KB_PATH = ROOT / "data" / "knowledge_base.json"
CONVEX_URL = os.getenv("CONVEX_URL")


def _norm_for_hash(text: str) -> str:
    t = re.sub(r"\s+", " ", text.lower().strip())
    return t[:800]


def _hash(text: str) -> str:
    return hashlib.sha1(_norm_for_hash(text).encode("utf-8")).hexdigest()[:12]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-convex", action="store_true")
    args = parser.parse_args()

    with open(KB_PATH, encoding="utf-8") as f:
        kb = json.load(f)
    entries = kb["entries"]
    print(f"Before: {len(entries)} entries")

    by_hash: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        h = _hash(e["answer"])
        by_hash[h].append(e)

    keep: list[dict] = []
    dropped = 0
    promoted_to_universal = 0

    for h, group in by_hash.items():
        if len(group) == 1:
            keep.append(group[0])
            continue

        models = {e["model"] for e in group}
        if len(models) == 1 and "universal" in models:
            keep.append(group[0])
            dropped += len(group) - 1
            continue

        non_universal = [e for e in group if e["model"] != "universal"]
        universals = [e for e in group if e["model"] == "universal"]

        if universals:
            keep.append(universals[0])
            dropped += len(universals) - 1 + len(non_universal)
            continue

        survivor = sorted(non_universal, key=lambda e: e["id"])[0]
        all_keywords = set()
        for e in non_universal:
            for kw in e["keywords"]:
                all_keywords.add(kw)
        survivor = dict(survivor)
        survivor["model"] = "universal"
        survivor["keywords"] = sorted(all_keywords)[:25]
        survivor["id"] = re.sub(r"^kb_[^_]+_[^_]+_", "kb_universal_", survivor["id"])
        keep.append(survivor)
        dropped += len(non_universal) - 1
        promoted_to_universal += 1

    kept_ids = {e["id"] for e in keep}
    print(f"After: {len(keep)} entries  (dropped {dropped}, promoted {promoted_to_universal} groups to universal)")

    if args.dry_run:
        return

    kb["entries"] = keep
    with open(KB_PATH, "w", encoding="utf-8") as f:
        json.dump(kb, f, ensure_ascii=False, indent=2)
    print(f"✓ Wrote dedupe'd KB to {KB_PATH}")

    if not args.no_convex and CONVEX_URL:
        from convex import ConvexClient
        client = ConvexClient(CONVEX_URL)
        print("Re-seeding Convex from clean KB...")
        client.mutation("kb:clearAll", {})
        for cat in kb["categories"]:
            client.mutation("kb:upsertCategory", {"name": cat})
        for i, e in enumerate(keep, 1):
            client.mutation("kb:upsertEntry", {
                "entryId": e["id"],
                "category": e["category"],
                "keywords": e["keywords"],
                "question": e["question"],
                "answer": e["answer"],
                "model": e["model"],
            })
            if i % 50 == 0:
                print(f"  ...{i}/{len(keep)}")
        print(f"✓ Pushed {len(keep)} to Convex")


if __name__ == "__main__":
    main()
