"""Shared session-scoped fixtures to prevent concurrent Qdrant file locks."""

import os
import pytest

os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("OLLAMA_URL", "http://localhost:11434/api/generate")
os.environ.setdefault("DISABLE_LLAMA", "1")


@pytest.fixture(scope="session")
def retriever():
    from src.retriever import build_default_retriever, CHUNKS_COLLECTION
    r = build_default_retriever()
    if r.count(CHUNKS_COLLECTION) == 0:
        pytest.skip("kb_chunks empty — run scripts/ingest_kb.py")
    return r


@pytest.fixture(scope="session")
def assistant(retriever):
    from src.assistant import CoffeeBotAssistant
    a = CoffeeBotAssistant.__new__(CoffeeBotAssistant)
    from src.knowledge_base import KnowledgeBase
    from src.classifier import Classifier
    from src.generator import Generator
    from src.retriever import CHUNKS_COLLECTION
    a.kb = KnowledgeBase()
    a.retriever = retriever
    a.classifier = Classifier(a.kb, retriever=retriever)
    a.generator = Generator()
    a.chunk_threshold = 0.45
    a.known_brands = []
    try:
        if retriever.count(CHUNKS_COLLECTION) > 0:
            a.known_brands = retriever.list_brands()
    except Exception:
        pass
    return a
