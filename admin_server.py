"""
Admin API for CaffeCare admin panel.

Run from the Diploma_CaffeBot directory:
    pip install fastapi uvicorn
    uvicorn admin_server:app --port 8001 --reload

Env: ADMIN_TOKEN overrides the default token (must match ADMIN_PASSWORD in .env.local).
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

ADMIN_TOKEN: str = os.getenv("ADMIN_TOKEN", "caffelab2026")

app = FastAPI(title="CaffeCare Admin", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_retriever = None


def _get_retriever():
    global _retriever
    if _retriever is None:
        from src.retriever import build_default_retriever

        print("[admin_server] loading encoder + connecting to Qdrant…")
        _retriever = build_default_retriever()
        print("[admin_server] ready")
    return _retriever


def _auth(token: str | None) -> None:
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/stats")
def stats(x_admin_token: str | None = Header(default=None)):
    _auth(x_admin_token)
    from src.retriever import CHUNKS_COLLECTION, QA_COLLECTION

    r = _get_retriever()
    return {
        "kb_qa": r.count(QA_COLLECTION),
        "kb_chunks": r.count(CHUNKS_COLLECTION),
    }


class SearchReq(BaseModel):
    query: str
    collection: str = "kb_chunks"
    model: str | None = None
    k: int = 5


@app.post("/search")
def search(req: SearchReq, x_admin_token: str | None = Header(default=None)):
    _auth(x_admin_token)
    from src.retriever import CHUNKS_COLLECTION, QA_COLLECTION

    r = _get_retriever()
    if req.collection == "kb_qa":
        hits = r.search_qa(req.query, model=req.model, k=req.k)
    else:
        hits = r.search_chunks(req.query, model=req.model, k=req.k)

    return {
        "query": req.query,
        "collection": req.collection,
        "hits": [{"score": round(h.score, 4), "payload": h.payload} for h in hits],
    }
