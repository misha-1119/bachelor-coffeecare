"""Telegram bot for CoffeeCare — guided diagnostic flow.

Commands:
  /start    — onboarding + home
  /menu     — home dashboard
  /model    — change machine
  /profile  — profile view
  /reset    — clear state, restart onboarding
  /help     — usage hint
"""

import os
import asyncio
import logging
import threading
import time
import traceback
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove,
    BotCommand,
)
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from src.assistant import CoffeeBotAssistant
from src.user_repository import UserRepository
from src.diagnostic_tree import get_tree, Category, Node, Option, Leaf
from src.brand_matcher import suggest_models, detect_brand, normalize_model_slug

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# In-memory per-user runtime state. Persistent fields live in Convex.
# state shape:
#   stage: "awaiting_name" | "awaiting_model" | "awaiting_free_text" | "ready"
#   name: str | None
#   machine: str | None             # slug
#   machine_display: str | None     # human label
#   flow: dict | None
#       cat: str
#       path: list[tuple[str, str]]  # (node_id, option_id_or_input)
#       awaiting_node: str | None    # node id awaiting free-text input
USER_STATE: dict[int, dict] = {}

# Per-user last AI result for "more detail" callback.
USER_CONVERSATION: dict[int, dict] = {}

TG_MSG_LIMIT = 4096


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _split_for_telegram(text: str, limit: int = TG_MSG_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind(" ", 0, limit)
        if cut == -1 or cut < limit // 2:
            cut = limit
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts


async def _send_long(send_fn, text: str, **kwargs):
    chunks = _split_for_telegram(text)
    reply_markup = kwargs.pop("reply_markup", None)
    for idx, chunk in enumerate(chunks):
        if idx == len(chunks) - 1 and reply_markup is not None:
            await send_fn(chunk, reply_markup=reply_markup, **kwargs)
        else:
            await send_fn(chunk, **kwargs)


_assistant: CoffeeBotAssistant | None = None
_user_repo: UserRepository | None = None


def _get_assistant() -> CoffeeBotAssistant:
    global _assistant
    if _assistant is None:
        _assistant = CoffeeBotAssistant()
    return _assistant


def _get_user_repo() -> UserRepository:
    global _user_repo
    if _user_repo is None:
        _user_repo = UserRepository()
    return _user_repo


def _ensure_state(user_id: int) -> dict:
    state = USER_STATE.get(user_id)
    if state is None:
        state = {
            "stage": "ready",
            "name": None,
            "machine": None,
            "machine_display": None,
            "flow": None,
        }
        USER_STATE[user_id] = state
    return state


def _reset_flow(state: dict) -> None:
    state["flow"] = None
    if state.get("stage") == "awaiting_free_text":
        state["stage"] = "ready"


# ---------- Keyboards ----------


def kb_add_machine() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Додати модель", callback_data="machine:add")],
        [InlineKeyboardButton("Пропустити", callback_data="machine:skip")],
    ])


def kb_model_suggestions(suggestions: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for s in suggestions[:3]:
        slug = normalize_model_slug(s)
        rows.append([InlineKeyboardButton(s, callback_data=f"machine:confirm:{slug}")])
    rows.append([InlineKeyboardButton("Інша модель", callback_data="machine:retry")])
    rows.append([InlineKeyboardButton("Пропустити", callback_data="machine:skip")])
    return InlineKeyboardMarkup(rows)


def kb_unknown_brand() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Спробувати ще", callback_data="machine:retry")],
        [InlineKeyboardButton("Загальні поради", callback_data="machine:skip")],
    ])


def kb_home() -> InlineKeyboardMarkup:
    tree = get_tree()
    buttons = tree.category_buttons()
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for key, label in buttons:
        pair.append(InlineKeyboardButton(label, callback_data=f"diag:{key}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([InlineKeyboardButton("Профіль", callback_data="profile:view")])
    return InlineKeyboardMarkup(rows)


def kb_node_options(cat: Category, node_id: str, node: Node) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for opt in node.options:
        pair.append(InlineKeyboardButton(opt.label, callback_data=f"flow:{node_id}:{opt.id}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([
        InlineKeyboardButton("Назад", callback_data="nav:back"),
        InlineKeyboardButton("Меню", callback_data="nav:home"),
    ])
    return InlineKeyboardMarkup(rows)


def kb_free_text_node() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Назад", callback_data="nav:back"),
            InlineKeyboardButton("Меню", callback_data="nav:home"),
        ],
    ])


def kb_post_answer() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Дізнатись детальніше", callback_data="ai:more")],
        [InlineKeyboardButton("Проблема вирішена", callback_data="ai:done")],
        [InlineKeyboardButton("Назад", callback_data="nav:home")],
    ])


def kb_profile() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Змінити модель", callback_data="machine:change")],
        [InlineKeyboardButton("Видалити профіль", callback_data="profile:delete")],
        [InlineKeyboardButton("Головне меню", callback_data="nav:home")],
    ])


def kb_profile_delete_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Так, видалити", callback_data="profile:delete:confirm")],
        [InlineKeyboardButton("Скасувати", callback_data="profile:view")],
    ])


# ---------- Rendering ----------


def _display_machine(slug: str | None, display: str | None = None) -> str:
    if display:
        return display
    if not slug or slug == "universal":
        return "не вказано"
    return slug.replace("_", " ").strip().title()


def _days_since(ts_ms: int | None) -> int | None:
    if not ts_ms:
        return None
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        delta = datetime.now(tz=timezone.utc) - dt
        return max(0, delta.days)
    except Exception:
        return None


async def send_welcome(send_fn, user_id: int, username: str | None) -> None:
    repo = _get_user_repo()
    await asyncio.to_thread(repo.upsert_user, user_id, username)
    row = await asyncio.to_thread(repo.get_user, user_id) or {}
    state = _ensure_state(user_id)
    state["name"] = row.get("name")
    state["machine"] = row.get("machine")
    state["machine_display"] = None  # rehydrate later via slug → display
    state["flow"] = None
    USER_CONVERSATION.pop(user_id, None)

    if not state["name"]:
        state["stage"] = "awaiting_name"
        await send_fn(
            "Привіт! Я CoffeeCare AI.\n"
            "Допомагаю діагностувати поломки, пояснювати ремонт, "
            "розшифровувати коди помилок і знаходити інструкції.\n\n"
            "Як до вас звертатись?",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if not state["machine"] or state["machine"] == "universal":
        state["stage"] = "ready"
        await send_fn(
            f"Супер, {state['name']}\n"
            "Давайте додамо вашу кавомашину — тоді я зможу давати точніші відповіді.",
            reply_markup=kb_add_machine(),
        )
        return

    await send_home(send_fn, state)


async def send_home(send_fn, state: dict) -> None:
    state["stage"] = "ready"
    state["flow"] = None
    machine_label = _display_machine(state.get("machine"), state.get("machine_display"))
    if state.get("machine") and state["machine"] != "universal":
        header = f"Машина підключена: {machine_label}\n\nЩо сталося?"
    else:
        header = "Машина не вказана.\nЩо сталося?"
    await send_fn(header, reply_markup=kb_home())


async def render_node(send_fn, state: dict, cat_key: str, node_id: str) -> None:
    tree = get_tree()
    cat = tree.category(cat_key)
    node = cat.node(node_id)
    if node.options:
        state["stage"] = "ready"
        state["flow"]["awaiting_node"] = None
        await send_fn(node.prompt, reply_markup=kb_node_options(cat, node_id, node))
        return
    if node.input == "free_text":
        state["stage"] = "awaiting_free_text"
        state["flow"]["awaiting_node"] = node_id
        await send_fn(node.prompt, reply_markup=kb_free_text_node())
        return
    # node with neither options nor input — treat as terminal informational
    await send_fn(node.prompt, reply_markup=kb_post_answer())


def _confidence_label(score: float) -> str:
    pct = int(round(score * 100))
    if score >= 0.75:
        tier = "Висока"
    elif score >= 0.5:
        tier = "Середня"
    else:
        tier = "Низька"
    return f"{tier} ({pct}%)"


async def run_ai(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int,
                 state: dict, leaf: Leaf, summary_text: str) -> None:
    """Stream progress edits, run assistant, emit final response."""
    placeholder = await ctx.bot.send_message(
        chat_id=chat_id,
        text="Аналізую симптоми…",
    )

    async def edit(text: str) -> None:
        try:
            await ctx.bot.edit_message_text(
                chat_id=chat_id,
                message_id=placeholder.message_id,
                text=text,
            )
        except BadRequest:
            pass

    progress_done = asyncio.Event()

    async def progress_loop() -> None:
        steps = [
            (1.2, "Перевіряю типові проблеми…"),
            (1.2, "Шукаю в інструкціях…"),
            (1.5, "Формую рішення…"),
        ]
        for delay, msg in steps:
            try:
                await asyncio.wait_for(progress_done.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                await edit(msg)

    pump = asyncio.create_task(progress_loop())

    assistant = _get_assistant()
    machine = state.get("machine") or "universal"
    name = state.get("name")
    conversation = USER_CONVERSATION.setdefault(user_id, {})
    try:
        result = await asyncio.to_thread(
            assistant.respond,
            summary_text,
            name,
            machine,
            conversation,
            None,  # user_bio dropped
            None,  # debug_log dropped
        )
    except Exception as e:
        log.exception("respond() failed for user %s: %s", user_id, e)
        result = {
            "response": "Не вдалося обробити запит. Спробуйте /menu.",
            "source": "error",
            "category": None,
            "confidence": 0.0,
            "kb_entry_id": None,
        }
    finally:
        progress_done.set()
        try:
            await pump
        except Exception:
            pass

    confidence_score = float(result.get("confidence") or 0.0)
    body = result["response"]
    final = (
        f"{body}\n\n"
        f"<b>Впевненість AI:</b> {_confidence_label(confidence_score)}\n"
        f"<b>Складність ремонту:</b> {leaf.complexity}"
    )

    # Replace placeholder with the final answer (or send fresh if too long).
    chunks = _split_for_telegram(final)
    if len(chunks) == 1:
        try:
            await ctx.bot.edit_message_text(
                chat_id=chat_id,
                message_id=placeholder.message_id,
                text=chunks[0],
                parse_mode="HTML",
                reply_markup=kb_post_answer(),
            )
        except BadRequest:
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=chunks[0],
                parse_mode="HTML",
                reply_markup=kb_post_answer(),
            )
    else:
        try:
            await ctx.bot.delete_message(chat_id=chat_id, message_id=placeholder.message_id)
        except BadRequest:
            pass
        for idx, chunk in enumerate(chunks):
            kwargs = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}
            if idx == len(chunks) - 1:
                kwargs["reply_markup"] = kb_post_answer()
            await ctx.bot.send_message(**kwargs)

    # Remember context for "more detail" / "done" callbacks.
    conversation["last_complexity"] = leaf.complexity
    conversation["last_category"] = state.get("flow", {}).get("cat") if state.get("flow") else None
    state["stage"] = "ready"


async def show_profile(send_fn, user_id: int, username: str | None) -> None:
    repo = _get_user_repo()
    await asyncio.to_thread(repo.upsert_user, user_id, username)
    row = await asyncio.to_thread(repo.get_user, user_id) or {}
    state = _ensure_state(user_id)

    name = row.get("name") or state.get("name") or "не вказано"
    machine_slug = row.get("machine") or state.get("machine")
    machine = _display_machine(machine_slug)
    days = _days_since(row.get("machineAddedAt"))
    diag_count = row.get("diagnosticCount") or 0
    top_cats = _get_user_repo().top_categories(row, n=2)

    tree = get_tree()
    cat_label = {k: lbl for k, lbl in tree.category_buttons()}

    lines = [
        f"<b>{_html_escape(name)}</b>",
        "",
        f"Кавомашина: {_html_escape(machine)}",
    ]
    if days is not None and machine_slug and machine_slug != "universal":
        lines.append(f"Додано: {days} {'день' if days == 1 else 'днів'} тому")
    lines.append(f"Діагностик: {diag_count}")

    if top_cats:
        lines.append("")
        lines.append("Часті проблеми:")
        for cat_key, count in top_cats:
            label = cat_label.get(cat_key, cat_key)
            lines.append(f"  {label} — {count}")

    await send_fn("\n".join(lines), parse_mode="HTML", reply_markup=kb_profile())


# ---------- Commands ----------


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_STATE.pop(user_id, None)
    USER_CONVERSATION.pop(user_id, None)
    await send_welcome(
        update.message.reply_text,
        user_id,
        update.effective_user.username,
    )


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = _ensure_state(user_id)
    if not state.get("name") or not state.get("machine"):
        await send_welcome(update.message.reply_text, user_id, update.effective_user.username)
        return
    await send_home(update.message.reply_text, state)


async def cmd_model(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = _ensure_state(user_id)
    state["stage"] = "awaiting_model"
    state["flow"] = None
    await update.message.reply_text(
        "Напишіть бренд і модель кавомашини.\n"
        "Напр.: Philips EP5447, DeLonghi Magnifica S, Jura E8.",
        reply_markup=kb_unknown_brand(),
    )


async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await show_profile(
        update.message.reply_text,
        update.effective_user.id,
        update.effective_user.username,
    )


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_STATE.pop(user_id, None)
    USER_CONVERSATION.pop(user_id, None)
    await cmd_start(update, ctx)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>CoffeeCare AI</b>\n\n"
        "Команди:\n"
        "/start — почати знову\n"
        "/menu — головне меню\n"
        "/model — змінити кавомашину\n"
        "/profile — мій профіль\n"
        "/reset — очистити стан\n"
        "/help — ця довідка\n\n"
        "Користуйтесь кнопками — оберіть категорію проблеми і уточнюйте крок за кроком."
    )
    await update.message.reply_text(text, parse_mode="HTML")


# ---------- Callback routing ----------


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data or ""
    state = _ensure_state(user_id)
    chat_id = query.message.chat_id if query.message else user_id

    parts = data.split(":")
    ns = parts[0] if parts else ""

    if ns == "machine":
        await _handle_machine(query, state, parts[1:], user_id)
        return

    if ns == "diag":
        if len(parts) < 2:
            return
        cat_key = parts[1]
        try:
            cat = get_tree().category(cat_key)
        except KeyError:
            await query.message.reply_text("Невідома категорія. /menu")
            return
        state["flow"] = {"cat": cat_key, "path": [], "awaiting_node": None}
        await render_node(query.message.reply_text, state, cat_key, cat.root)
        return

    if ns == "flow":
        if len(parts) < 3 or not state.get("flow"):
            await query.message.reply_text("Сесія діагностики скинута. /menu")
            return
        node_id, opt_id = parts[1], parts[2]
        cat_key = state["flow"]["cat"]
        try:
            opt = get_tree().resolve_option(cat_key, node_id, opt_id)
        except KeyError:
            await query.message.reply_text("Невідомий варіант. /menu")
            return
        state["flow"]["path"].append((node_id, opt_id))
        if opt.leaf:
            summary = get_tree().collect_summary(cat_key, state["flow"]["path"])
            await run_ai(ctx, chat_id, user_id, state, opt.leaf, summary)
            return
        if opt.next:
            await render_node(query.message.reply_text, state, cat_key, opt.next)
            return
        await query.message.reply_text("Гілка закінчилась без leaf. /menu")
        return

    if ns == "nav":
        action = parts[1] if len(parts) > 1 else ""
        if action == "home":
            _reset_flow(state)
            await send_home(query.message.reply_text, state)
            return
        if action == "back":
            flow = state.get("flow")
            if not flow or not flow.get("path"):
                _reset_flow(state)
                await send_home(query.message.reply_text, state)
                return
            flow["path"].pop()
            if not flow["path"]:
                cat = get_tree().category(flow["cat"])
                await render_node(query.message.reply_text, state, flow["cat"], cat.root)
            else:
                prev_node_id, prev_opt_id = flow["path"][-1]
                prev_opt = get_tree().resolve_option(flow["cat"], prev_node_id, prev_opt_id)
                if prev_opt.next:
                    await render_node(query.message.reply_text, state, flow["cat"], prev_opt.next)
                else:
                    # previous was a leaf — drop it and re-render parent
                    flow["path"].pop()
                    if flow["path"]:
                        prev_node_id, _ = flow["path"][-1]
                        await render_node(query.message.reply_text, state, flow["cat"], prev_node_id)
                    else:
                        cat = get_tree().category(flow["cat"])
                        await render_node(query.message.reply_text, state, flow["cat"], cat.root)
            return

    if ns == "ai":
        action = parts[1] if len(parts) > 1 else ""
        if action == "more":
            conversation = USER_CONVERSATION.get(user_id, {})
            last_chunk = conversation.get("last_chunk")
            if last_chunk and last_chunk.get("text"):
                text = last_chunk["text"]
                file = last_chunk.get("file", "")
                page = last_chunk.get("page_start")
                cite = f"\n\n<i>Джерело: {_html_escape(file)}"
                if page:
                    cite += f", стор. {page}"
                cite += ".</i>"
                await _send_long(
                    query.message.reply_text,
                    text + cite,
                    parse_mode="HTML",
                    reply_markup=kb_post_answer(),
                )
                return
            last_id = conversation.get("last_entry_id")
            if last_id:
                kb = _get_assistant().kb
                entry = next((e for e in kb.entries if e.id == last_id), None)
                if entry:
                    await _send_long(
                        query.message.reply_text,
                        entry.answer,
                        reply_markup=kb_post_answer(),
                    )
                    return
            await query.message.reply_text(
                "Поки немає додаткових деталей. Опишіть проблему конкретніше через /menu.",
                reply_markup=kb_post_answer(),
            )
            return
        if action == "done":
            conversation = USER_CONVERSATION.get(user_id, {})
            cat_key = conversation.get("last_category")
            if cat_key:
                await asyncio.to_thread(_get_user_repo().increment_diagnostic, user_id, cat_key)
            USER_CONVERSATION.pop(user_id, None)
            _reset_flow(state)
            await query.message.reply_text("Радий допомогти! Якщо потрібно ще — /menu")
            return

    if ns == "profile":
        action = parts[1] if len(parts) > 1 else ""
        if action == "view":
            await show_profile(
                lambda text, **kw: query.message.reply_text(text, **kw),
                user_id,
                query.from_user.username,
            )
            return
        if action == "delete":
            sub = parts[2] if len(parts) > 2 else ""
            if sub == "confirm":
                ok = await asyncio.to_thread(_get_user_repo().delete_user, user_id)
                USER_STATE.pop(user_id, None)
                USER_CONVERSATION.pop(user_id, None)
                msg = "Профіль видалено." if ok else "Профіль не знайдено."
                await query.message.reply_text(msg + " Напишіть /start для нового початку.")
                return
            await query.message.reply_text(
                "Видалити профіль повністю? Цю дію не можна скасувати.",
                reply_markup=kb_profile_delete_confirm(),
            )
            return

    await query.message.reply_text("Не зрозумів дію. /menu")


async def _handle_machine(query, state: dict, args: list[str], user_id: int) -> None:
    action = args[0] if args else ""

    if action == "add":
        state["stage"] = "awaiting_model"
        state["flow"] = None
        await query.message.reply_text(
            "Напишіть бренд і модель кавомашини.\n"
            "Напр.: Philips EP5447, DeLonghi Magnifica S, Jura E8.",
        )
        return

    if action == "skip":
        state["machine"] = "universal"
        state["machine_display"] = None
        state["stage"] = "ready"
        await asyncio.to_thread(_get_user_repo().set_machine, user_id, "universal")
        await send_home(query.message.reply_text, state)
        return

    if action == "retry":
        state["stage"] = "awaiting_model"
        state["flow"] = None
        await query.message.reply_text(
            "Введіть модель кавомашини ще раз. Напр.: Philips EP2231, DeLonghi Magnifica.",
        )
        return

    if action == "change":
        state["stage"] = "awaiting_model"
        state["flow"] = None
        await query.message.reply_text(
            "Напишіть нову модель кавомашини.\n"
            "Напр.: Philips EP5447, DeLonghi Magnifica S, Jura E8.",
        )
        return

    if action == "confirm" and len(args) >= 2:
        slug = args[1]
        display = slug.replace("_", " ").title()
        state["machine"] = slug
        state["machine_display"] = display
        state["stage"] = "ready"
        await asyncio.to_thread(_get_user_repo().set_machine, user_id, slug)
        await send_home(query.message.reply_text, state)
        return


# ---------- Message handler (free-text only in awaiting_* states) ----------


_SKIP_TOKENS = {
    "не знаю", "не знаю.", "не пам'ятаю", "не пам’ятаю",
    "немає", "нема", "хз", "skip", "пропустити", "пропустити.",
    "—", "-", "?", "??",
}


def _looks_like_skip(text: str) -> bool:
    return text.strip().lower() in _SKIP_TOKENS


def _normalize_name(text: str) -> str | None:
    cleaned = text.strip().replace("\n", " ")
    if not cleaned:
        return None
    return cleaned[:30].rstrip() or None


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""
    state = _ensure_state(user_id)
    repo = _get_user_repo()
    await asyncio.to_thread(repo.upsert_user, user_id, update.effective_user.username)
    chat_id = update.effective_chat.id

    stage = state.get("stage")

    if stage == "awaiting_name":
        name = _normalize_name(text)
        if not name:
            await update.message.reply_text("Напишіть, будь ласка, ім'я (1–30 символів).")
            return
        state["name"] = name
        state["stage"] = "ready"
        await asyncio.to_thread(repo.set_name, user_id, name)
        await update.message.reply_text(
            f"Супер, {name}\n"
            "Давайте додамо вашу кавомашину — тоді я зможу давати точніші відповіді.",
            reply_markup=kb_add_machine(),
        )
        return

    if stage == "awaiting_model":
        if _looks_like_skip(text):
            state["machine"] = "universal"
            state["machine_display"] = None
            state["stage"] = "ready"
            await asyncio.to_thread(repo.set_machine, user_id, "universal")
            await send_home(update.message.reply_text, state)
            return
        assistant = _get_assistant()
        known = assistant.known_brands or None
        suggestions = suggest_models(text, n=3, known_brands=known)
        if not suggestions:
            await update.message.reply_text(
                "Не вдалося визначити бренд.\nСпробуйте написати модель точніше.",
                reply_markup=kb_unknown_brand(),
            )
            return
        await update.message.reply_text(
            "Знайшов схожі моделі:",
            reply_markup=kb_model_suggestions(suggestions),
        )
        return

    if stage == "awaiting_free_text":
        flow = state.get("flow")
        if not flow or not flow.get("awaiting_node"):
            state["stage"] = "ready"
            await update.message.reply_text("Сесія скинута. /menu")
            return
        cat_key = flow["cat"]
        node_id = flow["awaiting_node"]
        tree = get_tree()
        node = tree.category(cat_key).node(node_id)
        if not node.leaf:
            state["stage"] = "ready"
            await update.message.reply_text("Помилка дерева. /menu")
            return
        free_text = text.strip()
        flow["path"].append((node_id, free_text))
        flow["awaiting_node"] = None
        summary = tree.collect_summary(cat_key, flow["path"])
        leaf = node.leaf
        # render summary line if template uses {input}
        if "{input}" in leaf.summary_template:
            extra = leaf.summary_template.format(input=free_text)
            summary = f"{summary}\nДеталі для AI: {extra}"
        await run_ai(ctx, chat_id, user_id, state, leaf, summary)
        return

    # Default: unexpected free text outside any awaiting state.
    await update.message.reply_text(
        "Користуйтесь кнопками меню — /menu",
        reply_markup=kb_home(),
    )


# ---------- App lifecycle ----------


def start_bot():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or token == "your_token_here":
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing or placeholder in .env")

    app = Application.builder().token(token).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)

    log.info("Bot starting...")
    threading.Thread(target=_warmup, daemon=True, name="warmup").start()
    app.run_polling()


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    err = ctx.error
    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__)) if err else ""
    log.error("Unhandled exception in handler: %s\n%s", err, tb)
    chat_id = None
    if isinstance(update, Update) and update.effective_chat:
        chat_id = update.effective_chat.id
    if chat_id is None:
        return
    try:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text="Сталася технічна помилка. Спробуйте /menu або /reset.",
        )
    except Exception as e:
        log.warning("Failed to notify user about error: %s", e)


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "Почати"),
        BotCommand("menu", "Головне меню"),
        BotCommand("model", "Змінити кавомашину"),
        BotCommand("profile", "Мій профіль"),
        BotCommand("reset", "Очистити стан"),
        BotCommand("help", "Довідка"),
    ])


def _warmup():
    t0 = time.monotonic()
    try:
        log.info("[warmup] loading assistant...")
        assistant = _get_assistant()
        log.info("[warmup] assistant ready (+%.2fs)", time.monotonic() - t0)
        if assistant.retriever is not None:
            try:
                from src.retriever import QA_COLLECTION  # noqa: F401
                assistant.retriever.search_qa("warmup", model=None, k=1)
                if assistant.known_brands:
                    assistant.retriever.search_chunks("warmup", model=None, k=1)
                log.info("[warmup] Qdrant warm (+%.2fs)", time.monotonic() - t0)
            except Exception as e:
                log.warning("[warmup] Qdrant ping failed: %s", e)
        try:
            assistant.generator.generate(
                user_query="тест",
                retrieved_instruction="тестова інструкція для прогріву моделі.",
                category="warmup",
            )
            log.info("[warmup] Generator hot (+%.2fs total)", time.monotonic() - t0)
        except Exception as e:
            log.warning("[warmup] Generator ping failed: %s", e)
    except Exception as e:
        log.warning("[warmup] failed: %s", e)


if __name__ == "__main__":
    start_bot()
