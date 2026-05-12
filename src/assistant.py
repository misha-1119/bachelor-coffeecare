"""
CoffeeBotAssistant: combines triage + Stage 1 (liberta-large) + Stage 2 (Lapa LLM).
"""

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

CONFIDENCE_THRESHOLD = 0.55
MIN_MEANINGFUL_LEN = 4


class CoffeeBotAssistant:
    def __init__(self):
        self.kb = KnowledgeBase()
        self.classifier = Classifier(self.kb)
        self.generator = Generator()

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
            _dbg("[more_detail] no last_entry → fallback")
            return self._fallback_response(
                user_name,
                "Спершу опишіть проблему — після відповіді можу розгорнути «детальніше».",
            )

        if is_negative_meta(user_query):
            _dbg("[triage] negative_meta → exclude last entry")
            tried = set(conversation.get("tried_ids", []))
            last_id = conversation.get("last_entry_id")
            if last_id:
                tried.add(last_id)
            conversation["tried_ids"] = list(tried)
            conversation["last_entry_id"] = None
            return self._instant("negative_meta", negative_meta_reply(user_name))

        if is_followup_no(user_query):
            _dbg("[triage] followup_no → exclude last entry")
            tried = set(conversation.get("tried_ids", []))
            last_id = conversation.get("last_entry_id")
            if last_id:
                tried.add(last_id)
            conversation["tried_ids"] = list(tried)
            conversation["last_entry_id"] = None
            return self._instant("followup_no", followup_no_reply(user_name))

        if is_followup_yes(user_query):
            _dbg("[triage] followup_yes")
            return self._instant("followup_yes", followup_yes_reply(user_name))

        if is_urgent_safety(user_query):
            _dbg("[triage] urgent_safety")
            return self._instant("safety", urgent_safety_reply(user_name))

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
            _dbg("[assistant] low confidence → fallback")
            return self._fallback_response(user_name)

        conversation["last_entry_id"] = entry.id

        if category == "clarify":
            _dbg("[assistant] category=clarify → kb_direct")
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
