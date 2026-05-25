"""
CoffeeBotAssistant: combines triage + Stage 1 (liberta-large) + Stage 2 (Lapa LLM).
"""

import os

from src.knowledge_base import KnowledgeBase
from src.classifier import Classifier
from src.generator import Generator
from src.triage import (
    normalize,
    is_greeting,
    is_goodbye,
    is_urgent_safety,
    is_followup_yes,
    is_followup_no,
    is_negative_meta,
    is_more_detail,
    greeting_reply,
    goodbye_reply,
    urgent_safety_reply,
    followup_yes_reply,
    followup_no_reply,
    negative_meta_reply,
)

CONFIDENCE_THRESHOLD = 1.0  # only error-code regex hits (conf=1.0) use KB directly; all else → chunks
KB_FALLBACK_THRESHOLD = 0.70  # min confidence to use KB answer when chunk fallback finds nothing
MIN_MEANINGFUL_LEN = 4


def _chunk_threshold() -> float:
    try:
        return float(os.getenv("CHUNK_THRESHOLD", "0.45"))
    except ValueError:
        return 0.45


def _try_build_retriever():
    """Open Qdrant if it has been ingested; return None and warn otherwise."""
    try:
        from src.retriever import build_default_retriever, QA_COLLECTION
    except Exception as exc:
        print(f"[assistant] retriever import failed ({exc}); using numpy KB path")
        return None
    try:
        retriever = build_default_retriever()
        if retriever.count(QA_COLLECTION) == 0:
            print("[assistant] kb_qa empty — run scripts/ingest_kb.py. Using numpy KB path.")
            return None
        return retriever
    except Exception as exc:
        print(f"[assistant] retriever init failed ({exc}); using numpy KB path")
        return None


def _resolve_brand(model_slug: str | None, known_brands: list[str]) -> str | None:
    """Match a user model slug to the longest known brand prefix.

    Examples:
        philips_ep2231         -> philips
        russell_hobbs_24370    -> russell_hobbs   (longer prefix wins over `russell`)
        delonghi_magnifica_s   -> delonghi
        universal / None       -> None
    """
    if not model_slug or model_slug == "universal" or not known_brands:
        return None
    candidates = [b for b in known_brands if model_slug == b or model_slug.startswith(b + "_")]
    if not candidates:
        return None
    candidates.sort(key=len, reverse=True)
    return candidates[0]


class CoffeeBotAssistant:
    def __init__(self):
        self.kb = KnowledgeBase()
        self.retriever = _try_build_retriever()
        self.classifier = Classifier(self.kb, retriever=self.retriever)
        self.generator = Generator()
        self.chunk_threshold = _chunk_threshold()
        self.known_brands: list[str] = []
        if self.retriever is not None:
            try:
                from src.retriever import CHUNKS_COLLECTION

                if self.retriever.count(CHUNKS_COLLECTION) > 0:
                    self.known_brands = self.retriever.list_brands()
                    print(f"[assistant] known brands: {self.known_brands}")
            except Exception as exc:
                print(f"[assistant] could not list brands ({exc})")

    def respond(
        self,
        user_query: str,
        user_name: str | None = None,
        machine_model: str | None = None,
        conversation: dict | None = None,
        user_bio: str | None = None,
        debug_log: list[str] | None = None,
    ) -> dict:
        def _dbg(msg: str) -> None:
            if debug_log is not None:
                debug_log.append(msg)

        if conversation is None:
            conversation = {}
        _dbg(f"[assistant] respond name={user_name!r} machine={machine_model!r} bio={'yes' if user_bio else 'no'} tried_ids={conversation.get('tried_ids', [])}")
        if not user_query or not user_query.strip():
            _dbg("[assistant] empty query → fallback")
            return self._fallback_response(user_name, "Напишіть, будь ласка, що сталося.")

        if is_greeting(user_query):
            _dbg("[triage] greeting")
            return self._instant("greeting", greeting_reply(user_name))

        if is_goodbye(user_query):
            _dbg("[triage] goodbye")
            return self._instant("goodbye", goodbye_reply(user_name))

        if is_more_detail(user_query):
            _dbg("[triage] more_detail")
            last_entry = self._get_entry_by_id(conversation.get("last_entry_id"))
            if last_entry is not None:
                _dbg(f"[more_detail] returning full kb entry={last_entry.id}")
                return {
                    "response": last_entry.answer,
                    "category": last_entry.category,
                    "kb_entry_id": last_entry.id,
                    "confidence": 1.0,
                    "source": "kb_full",
                }
            last_chunk = conversation.get("last_chunk")
            if last_chunk:
                _dbg(f"[more_detail] returning full last chunk file={last_chunk.get('file')}")
                file = last_chunk.get("file", "")
                page = last_chunk.get("page_start")
                text = last_chunk.get("text", "")
                citation = f"\n\n_Джерело: {file}"
                if page:
                    citation += f", стор. {page}"
                citation += "._"
                return {
                    "response": text + citation,
                    "category": "manual",
                    "kb_entry_id": None,
                    "confidence": 1.0,
                    "source": "manual_full",
                }
            _dbg("[more_detail] no last_entry → fallback")
            return self._fallback_response(
                user_name,
                "Спершу опишіть проблему — після відповіді можу розгорнути «детальніше».",
            )

        if is_negative_meta(user_query):
            _dbg("[triage] negative_meta → exclude last entry")
            self._mark_tried(conversation)
            return self._instant("negative_meta", negative_meta_reply(user_name))

        if is_followup_no(user_query):
            _dbg("[triage] followup_no → exclude last entry")
            self._mark_tried(conversation)
            return self._instant("followup_no", followup_no_reply(user_name))

        if is_followup_yes(user_query):
            _dbg("[triage] followup_yes")
            return self._instant("followup_yes", followup_yes_reply(user_name))

        if is_urgent_safety(user_query):
            _dbg("[triage] urgent_safety")
            return self._instant("safety", urgent_safety_reply(user_name))

        pending_clarify = conversation.pop("pending_clarify_query", None)
        if pending_clarify:
            user_query = f"{pending_clarify}. {user_query}"
            _dbg(f"[clarify] merged pending clarify → {user_query!r}")

        normalized = normalize(user_query)
        _dbg(f"[normalize] {normalized!r}")

        import re as _re
        cleaned = _re.sub(r"[^\w]+", "", normalized)
        is_error_code = bool(_re.fullmatch(r"[a-zа-я]\d{1,3}", cleaned, _re.IGNORECASE))
        if len(cleaned) < MIN_MEANINGFUL_LEN and not is_error_code:
            _dbg(f"[assistant] cleaned len={len(cleaned)} < {MIN_MEANINGFUL_LEN} → fallback")
            return self._fallback_response(user_name)
        if is_error_code:
            _dbg(f"[assistant] short input but error-code pattern {cleaned!r} → continue")

        exclude_ids = set(conversation.get("tried_ids", []))
        entry, confidence = self.classifier.get_best_match(
            normalized, machine_model, exclude_ids=exclude_ids or None
        )
        category = entry.category if entry else "general"
        _dbg(f"[classifier] entry={entry.id if entry else None} cat={category} conf={confidence:.3f} threshold={CONFIDENCE_THRESHOLD}")

        if entry is None or confidence < CONFIDENCE_THRESHOLD:
            chunk_response = self._try_chunk_fallback(
                user_query=user_query,
                normalized=normalized,
                machine_model=machine_model,
                user_name=user_name,
                user_bio=user_bio,
                conversation=conversation,
                debug_log=debug_log,
            )
            if chunk_response is not None:
                return chunk_response
            # Chunk fallback found nothing — try KB entry as last resort before generic fallback
            if entry is not None and confidence >= KB_FALLBACK_THRESHOLD:
                _dbg(f"[assistant] chunk miss → KB fallback (conf={confidence:.3f} entry={entry.id})")
                conversation["last_entry_id"] = entry.id
                if category == "clarify":
                    text = entry.answer
                    if user_name and not text.lower().startswith(user_name.lower()):
                        text = f"{user_name}, {text[0].lower()}{text[1:]}"
                    return {
                        "response": text,
                        "category": category,
                        "kb_entry_id": entry.id,
                        "confidence": confidence,
                        "source": "kb_fallback",
                    }
                response = self.generator.generate(
                    user_query=user_query,
                    retrieved_instruction=entry.answer,
                    category=category,
                    user_name=user_name,
                    user_bio=user_bio,
                )
                return {
                    "response": response,
                    "category": category,
                    "kb_entry_id": entry.id,
                    "confidence": confidence,
                    "source": "kb_fallback",
                }
            _dbg("[assistant] low confidence + no chunk hit → fallback")
            return self._fallback_response(user_name)

        conversation["last_entry_id"] = entry.id

        if category == "clarify":
            _dbg("[assistant] category=clarify → kb_direct")
            conversation["pending_clarify_query"] = user_query
            text = entry.answer
            if user_name and not text.lower().startswith(user_name.lower()):
                text = f"{user_name}, {text[0].lower()}{text[1:]}"
            return {
                "response": text,
                "category": category,
                "kb_entry_id": entry.id,
                "confidence": confidence,
                "source": "kb_direct",
            }

        gen_available = self.generator._is_available()
        _dbg(f"[generator] available={gen_available} model={self.generator.model}")
        response = self.generator.generate(
            user_query=user_query,
            retrieved_instruction=entry.answer,
            category=category,
            user_name=user_name,
            user_bio=user_bio,
        )
        _dbg(f"[generator] response_len={len(response)}")

        source = "lapa" if gen_available else "kb_direct"

        return {
            "response": response,
            "category": category,
            "kb_entry_id": entry.id,
            "confidence": confidence,
            "source": source,
        }

    def _mark_tried(self, conversation: dict) -> None:
        """Move last_entry_id and last_chunk into the tried-* sets."""
        tried = set(conversation.get("tried_ids", []))
        last_id = conversation.get("last_entry_id")
        if last_id:
            tried.add(last_id)
        conversation["tried_ids"] = list(tried)
        conversation["last_entry_id"] = None

        last_chunk = conversation.get("last_chunk")
        if last_chunk and last_chunk.get("chunk_id"):
            tried_chunks = set(conversation.get("tried_chunk_ids", []))
            tried_chunks.add(last_chunk["chunk_id"])
            conversation["tried_chunk_ids"] = list(tried_chunks)
        conversation["last_chunk"] = None

    def _try_chunk_fallback(
        self,
        user_query: str,
        normalized: str,
        machine_model: str | None,
        user_name: str | None,
        user_bio: str | None,
        conversation: dict,
        debug_log: list[str] | None,
    ) -> dict | None:
        if self.retriever is None:
            return None

        def _dbg(msg: str) -> None:
            if debug_log is not None:
                debug_log.append(msg)

        tried_chunk_ids = list(conversation.get("tried_chunk_ids", []))
        best = None
        tier = None  # "model" | "brand" | "universal"
        brand = _resolve_brand(machine_model, self.known_brands)
        # Brand list was cached at boot. If a user references a brand we
        # don't yet know about (post-boot ingest, manual transfer, etc.),
        # re-list once and retry.
        if brand is None and machine_model and machine_model != "universal":
            try:
                fresh = self.retriever.list_brands()
            except Exception as exc:
                fresh = []
                _dbg(f"[retriever] list_brands refresh failed ({exc})")
            if fresh and fresh != self.known_brands:
                self.known_brands = fresh
                brand = _resolve_brand(machine_model, self.known_brands)
                _dbg(f"[retriever] brand list refreshed ({len(fresh)} brands), resolved={brand!r}")

        # Tier 1: exact model + universal chunks
        try:
            hits = self.retriever.search_chunks(
                normalized, machine_model, k=3, exclude_chunk_ids=tried_chunk_ids or None
            )
        except Exception as exc:
            _dbg(f"[retriever] tier1 search failed ({exc})")
            hits = []
        if hits:
            _dbg(
                f"[retriever] tier1=model score={hits[0].score:.3f} "
                f"file={hits[0].payload.get('file')} page={hits[0].payload.get('page_start')}"
            )
            if hits[0].score >= self.chunk_threshold:
                best, tier = hits[0], "model"

        # Tier 2: same brand, excluding exact model (already tried)
        if best is None and brand:
            try:
                bhits = self.retriever.search_chunks_by_brand(
                    normalized,
                    brand=brand,
                    k=3,
                    exclude_model=machine_model,
                    exclude_chunk_ids=tried_chunk_ids or None,
                )
            except Exception as exc:
                _dbg(f"[retriever] tier2 search failed ({exc})")
                bhits = []
            if bhits:
                _dbg(
                    f"[retriever] tier2=brand={brand} score={bhits[0].score:.3f} "
                    f"file={bhits[0].payload.get('file')} page={bhits[0].payload.get('page_start')}"
                )
                if bhits[0].score >= self.chunk_threshold:
                    best, tier = bhits[0], "brand"

        # Tier 3: any brand. Skip when user has a known brand — surfacing a
        # different vendor's instructions is worse than asking the user to
        # clarify (W3 in the wiring audit).
        if best is None and brand is None:
            try:
                uhits = self.retriever.search_chunks(
                    normalized,
                    model=None,
                    k=3,
                    exclude_chunk_ids=tried_chunk_ids or None,
                )
            except Exception as exc:
                _dbg(f"[retriever] tier3 search failed ({exc})")
                uhits = []
            if uhits:
                _dbg(
                    f"[retriever] tier3=any score={uhits[0].score:.3f} "
                    f"file={uhits[0].payload.get('file')} page={uhits[0].payload.get('page_start')}"
                )
                if uhits[0].score >= self.chunk_threshold:
                    best, tier = uhits[0], "universal"

        if best is None or tier is None:
            _dbg("[retriever] chunk_fallback no hits above threshold")
            return None

        chunk_text = (best.payload.get("text") or "").strip()
        if not chunk_text:
            return None
        ref_file = best.payload.get("file", "")
        page = best.payload.get("page_start")
        hit_model = best.payload.get("model")
        chunk_id = best.payload.get("chunk_id")

        if tier == "model":
            citation = f"\n\n_Джерело: {ref_file}"
        elif tier == "brand":
            citation = f"\n\n_Джерело зі схожої моделі ({hit_model}): {ref_file}"
        else:
            citation = f"\n\n_Джерело: {ref_file}"
        if page:
            citation += f", стор. {page}"
        citation += "._"

        gen_available = self.generator._is_available()
        instruction = chunk_text if len(chunk_text) <= 1800 else chunk_text[:1800] + "..."
        if gen_available:
            response = self.generator.generate(
                user_query=user_query,
                retrieved_instruction=instruction,
                category="manual",
                user_name=user_name,
                user_bio=user_bio,
            )
        else:
            prefix = f"{user_name}, " if user_name else ""
            response = f"{prefix}знайшов у мануалі:\n\n{instruction}"
        response = f"{response}{citation}"

        conversation["last_entry_id"] = None
        conversation["last_chunk"] = {
            "chunk_id": chunk_id,
            "file": ref_file,
            "page_start": page,
            "text": chunk_text,
            "tier": tier,
        }
        return {
            "response": response,
            "category": "manual",
            "kb_entry_id": None,
            "confidence": float(best.score),
            "source": "lapa_manual" if gen_available else "manual_direct",
            "manual_tier": tier,
        }

    def _get_entry_by_id(self, entry_id: str | None):
        if not entry_id:
            return None
        for e in self.kb.entries:
            if e.id == entry_id:
                return e
        return None

    def _instant(self, kind: str, text: str) -> dict:
        return {
            "response": text,
            "category": kind,
            "kb_entry_id": None,
            "confidence": 1.0,
            "source": "triage",
        }

    def _fallback_response(self, user_name: str | None, override: str | None = None) -> dict:
        if override:
            text = override
        else:
            addr = f"{user_name}, " if user_name else ""
            text = (
                f"{addr}не вловив проблему — опишіть, будь ласка, конкретніше. "
                "Підкажіть: код помилки на дисплеї (якщо є), що саме відбувається з машиною — "
                "не вмикається, не варить, тече вода, дивний звук, проблема зі смаком?"
            )
        return {
            "response": text,
            "category": "clarify",
            "kb_entry_id": None,
            "confidence": 0.0,
            "source": "low_confidence",
        }
