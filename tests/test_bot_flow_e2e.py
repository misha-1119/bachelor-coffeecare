"""End-to-end-ish flow test for bot.py.

Mocks Telegram Update/CallbackQuery/Message + CoffeeBotAssistant + UserRepository.
Drives the new guided-diagnostic flow through every screen.
"""

import asyncio
import os
import sys
import types
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch, MagicMock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "fake")
os.environ.setdefault("DISABLE_LLAMA", "1")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import bot  # noqa: E402


@dataclass
class FakeUser:
    id: int = 999
    username: str = "tester"


@dataclass
class FakeChat:
    id: int = 999


@dataclass
class FakeMessage:
    chat_id: int = 999
    message_id: int = 1
    text: str = ""
    sent: list[dict] = field(default_factory=list)

    async def reply_text(self, text: str, **kwargs):
        self.sent.append({"text": text, **kwargs})
        return FakeMessage(chat_id=self.chat_id, message_id=self.message_id + 1, text=text)


@dataclass
class FakeUpdate:
    effective_user: FakeUser
    effective_chat: FakeChat
    message: FakeMessage | None = None
    callback_query: Any = None


@dataclass
class FakeCallbackQuery:
    data: str
    from_user: FakeUser
    message: FakeMessage
    answered: bool = False

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text: str, **kwargs):
        self.message.sent.append({"text": text, "edited": True, **kwargs})


class FakeBot:
    """Captures bot.send_message / edit_message_text / delete_message calls."""

    def __init__(self):
        self.sent: list[dict] = []
        self.edits: list[dict] = []
        self._next_id = 1000

    async def send_message(self, chat_id, text, **kwargs):
        self._next_id += 1
        rec = {"chat_id": chat_id, "text": text, "message_id": self._next_id, **kwargs}
        self.sent.append(rec)
        msg = MagicMock()
        msg.message_id = self._next_id
        return msg

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "text": text, **kwargs})

    async def delete_message(self, chat_id, message_id):
        pass

    async def send_chat_action(self, **kwargs):
        pass


class FakeContext:
    def __init__(self, bot_: FakeBot):
        self.bot = bot_
        self.error = None
        self.args = []


def _make_update_msg(text: str, user_id: int = 999):
    user = FakeUser(id=user_id)
    chat = FakeChat(id=user_id)
    msg = FakeMessage(chat_id=user_id, text=text)
    return FakeUpdate(effective_user=user, effective_chat=chat, message=msg)


def _make_update_cb(data: str, user_id: int = 999):
    user = FakeUser(id=user_id)
    chat = FakeChat(id=user_id)
    msg = FakeMessage(chat_id=user_id)
    cb = FakeCallbackQuery(data=data, from_user=user, message=msg)
    upd = FakeUpdate(effective_user=user, effective_chat=chat, callback_query=cb)
    return upd, msg


class FakeRepo:
    def __init__(self):
        self.rows: dict[int, dict] = {}
        self.diagnostic_calls: list[tuple[int, str]] = []
        self.deleted: list[int] = []

    def upsert_user(self, uid, username):
        self.rows.setdefault(uid, {"telegramUserId": uid, "telegramUsername": username, "messageCount": 0})

    def get_user(self, uid):
        return self.rows.get(uid)

    def set_name(self, uid, name):
        self.rows.setdefault(uid, {}).update({"name": name})

    def set_machine(self, uid, machine):
        self.rows.setdefault(uid, {}).update({"machine": machine, "machineAddedAt": 1_700_000_000_000})

    def increment_message_count(self, uid):
        row = self.rows.setdefault(uid, {})
        row["messageCount"] = row.get("messageCount", 0) + 1
        return row["messageCount"]

    def set_bio(self, *a, **kw): pass

    def increment_diagnostic(self, uid, category):
        self.diagnostic_calls.append((uid, category))
        row = self.rows.setdefault(uid, {})
        row["diagnosticCount"] = row.get("diagnosticCount", 0) + 1
        cats = row.setdefault("frequentCategories", [])
        for c in cats:
            if c["cat"] == category:
                c["count"] += 1
                break
        else:
            cats.append({"cat": category, "count": 1})
        return row["diagnosticCount"]

    def delete_user(self, uid):
        self.deleted.append(uid)
        return self.rows.pop(uid, None) is not None

    def top_categories(self, row, n=2):
        if not row:
            return []
        cats = row.get("frequentCategories") or []
        items = [(c["cat"], c["count"]) for c in cats]
        items.sort(key=lambda x: x[1], reverse=True)
        return items[:n]


class FakeAssistant:
    def __init__(self):
        self.kb = MagicMock()
        self.kb.entries = []
        self.retriever = None
        self.known_brands = ["philips", "delonghi", "jura", "saeco"]
        self.generator = MagicMock()

    def respond(self, query, name=None, machine=None, conversation=None, bio=None, debug=None):
        if conversation is not None:
            conversation["last_entry_id"] = "mock_entry"
        return {
            "response": f"Ймовірна причина: тест ({machine}).",
            "category": "leak",
            "kb_entry_id": "mock_entry",
            "confidence": 0.84,
            "source": "lapa",
        }


def _reset_singletons(repo: FakeRepo, assistant: FakeAssistant):
    bot._user_repo = repo
    bot._assistant = assistant
    bot.USER_STATE.clear()
    bot.USER_CONVERSATION.clear()


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def collect_button_labels(reply_markup) -> list[str]:
    if reply_markup is None:
        return []
    return [b.text for row in reply_markup.inline_keyboard for b in row]


def collect_button_data(reply_markup) -> list[str]:
    if reply_markup is None:
        return []
    return [b.callback_data for row in reply_markup.inline_keyboard for b in row]


# ---------- Tests ----------


def test_full_happy_path():
    repo = FakeRepo()
    assistant = FakeAssistant()
    _reset_singletons(repo, assistant)

    fbot = FakeBot()
    fctx = FakeContext(fbot)

    # 1) /start — new user
    upd = _make_update_msg("/start")
    run(bot.cmd_start(upd, fctx))
    assert any("Привіт" in s["text"] for s in upd.message.sent), upd.message.sent
    assert bot.USER_STATE[999]["stage"] == "awaiting_name"

    # 2) user types name
    upd = _make_update_msg("Михайло")
    run(bot.on_message(upd, fctx))
    out = upd.message.sent
    assert any("Супер, Михайло" in s["text"] for s in out), out
    labels = collect_button_labels(out[-1]["reply_markup"])
    assert "☕ Додати модель" in labels
    assert "⏭ Пропустити" in labels

    # 3) callback machine:add → ask for model
    upd, msg = _make_update_cb("machine:add")
    run(bot.on_callback(upd, fctx))
    assert any("Напишіть бренд" in s["text"] for s in msg.sent), msg.sent
    assert bot.USER_STATE[999]["stage"] == "awaiting_model"

    # 4) user types model → suggestions
    upd = _make_update_msg("philips ep5447")
    run(bot.on_message(upd, fctx))
    out = upd.message.sent
    assert any("схожі моделі" in s["text"] for s in out), out
    suggest_labels = collect_button_labels(out[-1]["reply_markup"])
    assert any("Philips" in l for l in suggest_labels), suggest_labels
    suggest_data = collect_button_data(out[-1]["reply_markup"])
    confirm_cb = next(d for d in suggest_data if d.startswith("machine:confirm:"))

    # 5) confirm chip
    upd, msg = _make_update_cb(confirm_cb)
    run(bot.on_callback(upd, fctx))
    assert any("Машина підключена" in s["text"] for s in msg.sent), msg.sent
    home_labels = collect_button_labels(msg.sent[-1]["reply_markup"])
    assert "💧 Вода / протікання" in home_labels
    assert bot.USER_STATE[999]["machine"].startswith("philips")

    # 6) pick leak category
    upd, msg = _make_update_cb("diag:leak")
    run(bot.on_callback(upd, fctx))
    assert any("Звідки" in s["text"] for s in msg.sent), msg.sent
    leak_data = collect_button_data(msg.sent[-1]["reply_markup"])
    assert "flow:where:bottom" in leak_data
    assert "nav:back" in leak_data
    assert "nav:home" in leak_data

    # 7) flow:where:bottom → next node "when"
    upd, msg = _make_update_cb("flow:where:bottom")
    run(bot.on_callback(upd, fctx))
    assert any("Коли" in s["text"] for s in msg.sent), msg.sent
    when_data = collect_button_data(msg.sent[-1]["reply_markup"])
    assert "flow:when:brewing" in when_data

    # 8) flow:when:brewing → leaf → run_ai
    upd, msg = _make_update_cb("flow:when:brewing")
    run(bot.on_callback(upd, fctx))
    # final answer comes via fbot.edits (edit_message_text) or fbot.sent
    finals = [e for e in fbot.edits if "Впевненість" in e.get("text", "")]
    finals += [s for s in fbot.sent if "Впевненість" in s.get("text", "")]
    assert finals, f"no final answer. edits={fbot.edits}, sent={fbot.sent}"
    final = finals[-1]
    assert "Висока (84%)" in final["text"]
    assert "Середня" in final["text"]  # complexity for leak/when=brewing leaf
    assert "Ймовірна причина: тест" in final["text"]

    # 9) ai:done — increments diagnostic count
    upd, msg = _make_update_cb("ai:done")
    run(bot.on_callback(upd, fctx))
    assert (999, "leak") in repo.diagnostic_calls

    # 10) /profile
    fbot.sent.clear()
    upd = _make_update_msg("/profile")
    run(bot.cmd_profile(upd, fctx))
    out = upd.message.sent
    text = out[-1]["text"]
    assert "Михайло" in text
    assert "Philips" in text
    assert "Діагностик: 1" in text
    assert "💧 Вода" in text
    profile_data = collect_button_data(out[-1]["reply_markup"])
    assert "profile:delete" in profile_data
    assert "machine:change" in profile_data

    # 11) delete confirm flow
    upd, msg = _make_update_cb("profile:delete")
    run(bot.on_callback(upd, fctx))
    assert any("Видалити профіль" in s["text"] for s in msg.sent), msg.sent

    upd, msg = _make_update_cb("profile:delete:confirm")
    run(bot.on_callback(upd, fctx))
    assert 999 in repo.deleted
    assert 999 not in bot.USER_STATE

    print("\n✅ full happy-path passed")


def test_error_code_subflow():
    repo = FakeRepo()
    repo.rows[888] = {
        "telegramUserId": 888,
        "name": "Olya",
        "machine": "delonghi_magnifica",
        "machineAddedAt": 1_700_000_000_000,
    }
    assistant = FakeAssistant()
    _reset_singletons(repo, assistant)
    fbot = FakeBot()
    fctx = FakeContext(fbot)

    # Re-hydrate state from /menu
    upd = _make_update_msg("/menu", user_id=888)
    run(bot.cmd_menu(upd, fctx))
    assert any("Машина підключена" in s["text"] for s in upd.message.sent), upd.message.sent

    # diag:error_code → brand picker
    upd, msg = _make_update_cb("diag:error_code", user_id=888)
    run(bot.on_callback(upd, fctx))
    brand_data = collect_button_data(msg.sent[-1]["reply_markup"])
    assert "flow:brand:delonghi" in brand_data

    # pick delonghi → free text node
    upd, msg = _make_update_cb("flow:brand:delonghi", user_id=888)
    run(bot.on_callback(upd, fctx))
    assert any("код помилки" in s["text"].lower() for s in msg.sent), msg.sent
    assert bot.USER_STATE[888]["stage"] == "awaiting_free_text"
    assert bot.USER_STATE[888]["flow"]["awaiting_node"] == "code_input"

    # user types E05 → runs AI
    upd = _make_update_msg("E05", user_id=888)
    run(bot.on_message(upd, fctx))
    finals = [e for e in fbot.edits if "Впевненість" in e.get("text", "")]
    finals += [s for s in fbot.sent if "Впевненість" in s.get("text", "")]
    assert finals, fbot.edits + fbot.sent

    print("✅ error_code subflow passed")


def test_nav_back():
    repo = FakeRepo()
    repo.rows[777] = {
        "telegramUserId": 777, "name": "A", "machine": "philips_ep5447",
        "machineAddedAt": 1_700_000_000_000,
    }
    _reset_singletons(repo, FakeAssistant())
    fctx = FakeContext(FakeBot())

    # enter leak → where → back → still in leak root
    upd = _make_update_msg("/menu", user_id=777)
    run(bot.cmd_menu(upd, fctx))

    upd, msg = _make_update_cb("diag:leak", user_id=777)
    run(bot.on_callback(upd, fctx))

    upd, msg = _make_update_cb("flow:where:bottom", user_id=777)
    run(bot.on_callback(upd, fctx))
    # now on `when` node
    assert any("Коли" in s["text"] for s in msg.sent)

    upd, msg = _make_update_cb("nav:back", user_id=777)
    run(bot.on_callback(upd, fctx))
    assert any("Звідки" in s["text"] for s in msg.sent), msg.sent

    # back again from empty path → home
    upd, msg = _make_update_cb("nav:back", user_id=777)
    run(bot.on_callback(upd, fctx))
    assert any("Машина підключена" in s["text"] for s in msg.sent), msg.sent
    print("✅ nav_back passed")


def test_unexpected_text_goes_home():
    repo = FakeRepo()
    repo.rows[666] = {
        "telegramUserId": 666, "name": "B", "machine": "universal",
    }
    _reset_singletons(repo, FakeAssistant())
    fctx = FakeContext(FakeBot())

    upd = _make_update_msg("/menu", user_id=666)
    run(bot.cmd_menu(upd, fctx))

    upd = _make_update_msg("просто текст без приводу", user_id=666)
    run(bot.on_message(upd, fctx))
    assert any("Користуйтесь кнопками" in s["text"] for s in upd.message.sent), upd.message.sent
    print("✅ unexpected_text_goes_home passed")


if __name__ == "__main__":
    test_full_happy_path()
    test_error_code_subflow()
    test_nav_back()
    test_unexpected_text_goes_home()
    print("\n🎉 all flow tests passed")
