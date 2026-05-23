"""
End-to-end flow tests: triage, assistant, retriever, clarify-merging, brand validation.
Run: pytest tests/test_flow.py -v
"""

import os
import pytest

# env defaults set in conftest.py


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def respond(assistant, query, conversation=None, machine=None, name="Тест"):
    if conversation is None:
        conversation = {}
    debug = []
    result = assistant.respond(
        query,
        user_name=name,
        machine_model=machine,
        conversation=conversation,
        debug_log=debug,
    )
    result["_debug"] = debug
    result["_conv"] = conversation
    return result


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------

class TestTriage:
    def test_greeting_ukrainian(self, assistant):
        r = respond(assistant, "Привіт!")
        assert r["category"] == "greeting"
        assert r["source"] == "triage"

    def test_greeting_english(self, assistant):
        r = respond(assistant, "Hello")
        assert r["category"] == "greeting"

    def test_goodbye(self, assistant):
        r = respond(assistant, "Дякую, до побачення")
        assert r["category"] == "goodbye"

    def test_urgent_safety(self, assistant):
        r = respond(assistant, "горить, дим іде з машини!")
        assert r["category"] == "safety"

    def test_empty_query_fallback(self, assistant):
        r = respond(assistant, "")
        assert r["confidence"] == 0.0

    def test_too_short_non_error_code(self, assistant):
        # "аж" = 2 chars, no triage match → length guard → fallback
        r = respond(assistant, "аж")
        assert r["confidence"] == 0.0


# ---------------------------------------------------------------------------
# Error code handling
# ---------------------------------------------------------------------------

class TestErrorCodes:
    def test_e02_direct(self, assistant):
        r = respond(assistant, "E02")
        assert r["kb_entry_id"] == "kb_002", f"Expected kb_002 got {r['kb_entry_id']}"
        assert r["category"] == "error_code"

    def test_e01_direct(self, assistant):
        r = respond(assistant, "E01")
        assert r["kb_entry_id"] == "kb_001", f"Expected kb_001 got {r['kb_entry_id']}"

    def test_error_phrase_no_water(self, assistant):
        # "немає води" may hit clarify (ask for more info) or error_code/brewing — all valid
        r = respond(assistant, "немає води, не варить каву")
        assert r["confidence"] >= 0.55
        assert r["category"] in ("error_code", "brewing", "manual", "clarify")


# ---------------------------------------------------------------------------
# Clarify flow + context merging (the E0* bug fix)
# ---------------------------------------------------------------------------

class TestClarifyMerging:
    def test_blinking_icon_triggers_clarify(self, assistant):
        conv = {}
        r = respond(assistant, "блимає якась іконка не пойму чого", conversation=conv)
        # With full KB loaded the bot may find real content (manual) or ask clarify.
        # Either is valid; regression is returning error-code kb_002 immediately.
        assert r["category"] in ("clarify", "manual", "chunk"), (
            f"Unexpected category: {r['category']}"
        )

    def test_followup_does_not_jump_to_e02(self, assistant):
        """Core regression: 'крапля води' after blinking-icon clarify must NOT resolve to kb_002."""
        conv = {}
        respond(assistant, "блимає якась іконка не пойму чого", conversation=conv)
        r2 = respond(assistant, "крапля води", conversation=conv)

        assert r2["kb_entry_id"] != "kb_002", (
            f"Regressed to E02! Got kb_entry_id={r2['kb_entry_id']} cat={r2['category']}"
        )

    def test_pending_clarify_consumed_after_followup(self, assistant):
        """pending_clarify_query is consumed each turn; if follow-up still hits clarify
        it is set again (chaining). Either way the KEY must NOT hold the raw original query."""
        conv = {}
        respond(assistant, "блимає якась іконка не пойму чого", conversation=conv)
        respond(assistant, "крапля води", conversation=conv)
        # If still set, it must be the MERGED query (not the original bare query)
        pending = conv.get("pending_clarify_query")
        if pending is not None:
            assert "крапля води" in pending, f"pending_clarify_query not merged: {pending!r}"

    def test_triage_hit_does_not_consume_pending(self, assistant):
        """A triage-handled follow-up (e.g. 'дякую') must NOT clear pending_clarify_query
        when the first turn set it. Skip if first turn didn't trigger clarify (has real KB content)."""
        conv = {}
        r1 = respond(assistant, "блимає якась іконка не пойму чого", conversation=conv)
        if r1["category"] != "clarify":
            pytest.skip("First turn returned real content (not clarify) — pending not set")
        respond(assistant, "дякую", conversation=conv)  # goodbye → triage
        assert conv.get("pending_clarify_query") is not None


# ---------------------------------------------------------------------------
# Retriever + Qdrant
# ---------------------------------------------------------------------------

class TestRetriever:
    def test_retriever_loaded(self, assistant):
        assert assistant.retriever is not None, "Retriever not initialized — run scripts/ingest_kb.py"

    def test_qa_collection_not_empty(self, assistant):
        from src.retriever import QA_COLLECTION
        count = assistant.retriever.count(QA_COLLECTION)
        assert count > 0, f"kb_qa collection empty (count={count})"

    def test_chunks_collection_not_empty(self, assistant):
        from src.retriever import CHUNKS_COLLECTION
        count = assistant.retriever.count(CHUNKS_COLLECTION)
        assert count > 0, f"kb_chunks collection empty (count={count})"

    def test_known_brands_loaded(self, assistant):
        assert len(assistant.known_brands) > 0, "No brands in kb_chunks"

    def test_search_qa_returns_hits(self, assistant):
        hits = assistant.retriever.search_qa("E02 немає води", k=3)
        assert len(hits) > 0
        assert hits[0].score > 0.3

    def test_search_chunks_returns_hits(self, assistant):
        hits = assistant.retriever.search_chunks("як очистити кавомашину", k=3)
        assert len(hits) > 0

    def test_search_chunks_by_brand(self, assistant):
        if "philips" not in assistant.known_brands:
            pytest.skip("philips brand not in KB")
        hits = assistant.retriever.search_chunks_by_brand("очищення", brand="philips", k=3)
        assert len(hits) > 0
        for h in hits:
            assert h.payload.get("brand") == "philips"


# ---------------------------------------------------------------------------
# Brand validation
# ---------------------------------------------------------------------------

class TestBrandValidation:
    def test_samsung_resolves_none(self, assistant):
        from src.assistant import _resolve_brand
        assert _resolve_brand("samsung", assistant.known_brands) is None

    def test_philips_ep2231_resolves(self, assistant):
        from src.assistant import _resolve_brand
        if "philips" not in assistant.known_brands:
            pytest.skip("philips not in KB")
        assert _resolve_brand("philips_ep2231", assistant.known_brands) == "philips"

    def test_delonghi_resolves(self, assistant):
        from src.assistant import _resolve_brand
        if "delonghi" not in assistant.known_brands:
            pytest.skip("delonghi not in KB")
        assert _resolve_brand("delonghi_magnifica_s", assistant.known_brands) == "delonghi"

    def test_universal_resolves_none(self, assistant):
        from src.assistant import _resolve_brand
        assert _resolve_brand("universal", assistant.known_brands) is None

    def test_none_input_resolves_none(self, assistant):
        from src.assistant import _resolve_brand
        assert _resolve_brand(None, assistant.known_brands) is None


# ---------------------------------------------------------------------------
# Machine-specific chunk retrieval
# ---------------------------------------------------------------------------

class TestMachineChunks:
    def test_known_brand_hits_chunks(self, assistant):
        if "philips" not in assistant.known_brands:
            pytest.skip("philips not in KB")
        conv = {}
        r = respond(assistant, "як очистити кавомашину", machine="philips_ep2231", conversation=conv)
        assert r["confidence"] >= 0.0  # just must not crash
        assert r["response"]

    def test_unknown_brand_still_gets_universal_chunks(self, assistant):
        """Unknown brand → _resolve_brand=None → Tier 3 universal search runs."""
        conv = {}
        r = respond(assistant, "як очистити кавомашину", machine="samsung", conversation=conv)
        # Should get a response (universal chunks), not crash
        assert r["response"]


# ---------------------------------------------------------------------------
# More-detail flow
# ---------------------------------------------------------------------------

class TestMoreDetail:
    def test_more_detail_without_prior_entry_returns_fallback(self, assistant):
        conv = {}
        r = respond(assistant, "детальніше", conversation=conv)
        assert r["source"] in ("triage", "low_confidence")

    def test_more_detail_after_kb_hit_returns_full_entry(self, assistant):
        conv = {}
        respond(assistant, "E02", conversation=conv)
        r2 = respond(assistant, "детальніше", conversation=conv)
        assert r2["source"] in ("kb_full", "triage")
        assert r2["confidence"] == 1.0


# ---------------------------------------------------------------------------
# Followup yes/no
# ---------------------------------------------------------------------------

class TestFollowup:
    def test_followup_no_marks_tried(self, assistant):
        conv = {}
        r1 = respond(assistant, "E02", conversation=conv)
        last_id = conv.get("last_entry_id")
        respond(assistant, "ні, не допомогло", conversation=conv)
        # last_id should now be in tried_ids (marked as tried)
        assert last_id in conv.get("tried_ids", []), (
            f"Expected {last_id!r} in tried_ids={conv.get('tried_ids')}"
        )
