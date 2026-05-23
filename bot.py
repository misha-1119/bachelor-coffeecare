"""
Telegram bot for CoffeeCare.

Commands:
  /start  — greet and start flow
  /model  — set machine brand/model
  /mode   — toggle NLP <-> rule_based per user
  /debug  — toggle per-user debug trace (/debug on|off, default from DEBUG_MODE env)
  /reset  — clear user state
  /eval   — dev only: run evaluation
"""

import os
import asyncio
import logging
import threading
import time
import traceback

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove,
    BotCommand,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from src.knowledge_base import KnowledgeBase
from src.assistant import CoffeeBotAssistant, _resolve_brand
from src.rule_based import RuleBasedAssistant
from src.user_repository import UserRepository
from src.bio_generator import generate_bio

BIO_TRIGGER_AT = 5
MAX_DEBUG_CHARS = 3500

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

USER_STATE: dict[int, dict] = {}
USER_MODE: dict[int, str] = {}
USER_CONVERSATION: dict[int, dict] = {}
USER_MESSAGES: dict[int, list[str]] = {}
USER_DEBUG: dict[int, bool] = {}


def _env_debug_default() -> bool:
    return os.getenv("DEBUG_MODE", "0").strip().lower() in {"1", "true", "yes", "on"}


def _debug_enabled(user_id: int) -> bool:
    return USER_DEBUG.get(user_id, _env_debug_default())


async def _send_debug(send_fn, user_id: int, steps: list[str]) -> None:
    if not _debug_enabled(user_id) or not steps:
        return
    body = "\n".join(steps)
    if len(body) > MAX_DEBUG_CHARS:
        body = body[:MAX_DEBUG_CHARS] + "\n…[truncated]"
    await send_fn(f"🐞 debug trace:\n<pre>{_html_escape(body)}</pre>", parse_mode="HTML")


async def _live_debug_pump(bot, chat_id: int, steps: list[str], started_at: float,
                            tick_interval: float = 1.5, heartbeat_every: float = 4.0):
    """Periodic pump: flushes new debug steps + keeps typing indicator alive + emits heartbeats.
    Cancel when work done."""
    last_idx = 0
    last_hb = started_at
    try:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        while True:
            await asyncio.sleep(tick_interval)
            now = time.monotonic()
            new = steps[last_idx:]
            if new:
                last_idx = len(steps)
                body = "\n".join(new)
                if len(body) > MAX_DEBUG_CHARS:
                    body = body[:MAX_DEBUG_CHARS] + "\n…[truncated]"
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"🐞 step (+{now - started_at:.1f}s):\n<pre>{_html_escape(body)}</pre>",
                        parse_mode="HTML",
                    )
                except Exception as e:
                    log.warning("live debug push failed: %s", e)
                last_hb = now
            elif now - last_hb >= heartbeat_every:
                try:
                    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                    await bot.send_message(chat_id=chat_id, text=f"🐞 working… +{now - started_at:.1f}s")
                except Exception as e:
                    log.warning("heartbeat failed: %s", e)
                last_hb = now
            else:
                try:
                    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                except Exception:
                    pass
    except asyncio.CancelledError:
        pass


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


TG_MSG_LIMIT = 4096


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
_rule_based: RuleBasedAssistant | None = None
_user_repo: UserRepository | None = None


def _get_assistant() -> CoffeeBotAssistant:
    global _assistant
    if _assistant is None:
        _assistant = CoffeeBotAssistant()
    return _assistant


def _get_rule_based() -> RuleBasedAssistant:
    global _rule_based
    if _rule_based is None:
        # Share the KB instance the NLP assistant already loaded so we don't pay
        # the Convex/JSON load twice on cold start.
        _rule_based = RuleBasedAssistant(_get_assistant().kb)
    return _rule_based


def _get_user_repo() -> UserRepository:
    global _user_repo
    if _user_repo is None:
        _user_repo = UserRepository()
    return _user_repo


def _ensure_state(user_id: int) -> dict:
    state = USER_STATE.get(user_id)
    if state is None:
        state = {"model": None, "stage": "ready"}
        USER_STATE[user_id] = state
    return state


def _kb_step1() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Додати кавомашину ☕", callback_data="action:add_machine")],
        [InlineKeyboardButton("Пропустити", callback_data="action:skip_machine")],
    ])


def _kb_step2() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Помилка на дисплеї", callback_data="category:error_code"),
            InlineKeyboardButton("Проблема з кавою", callback_data="category:brewing"),
        ],
        [InlineKeyboardButton("Обслуговування / чистка", callback_data="category:cleaning")],
        [InlineKeyboardButton("Інше", callback_data="category:general")],
    ])


def _kb_post_answer() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Детальніше", callback_data="action:more_detail"),
            InlineKeyboardButton("Інша проблема", callback_data="action:other_problem"),
        ],
        [
            InlineKeyboardButton("Почати знову", callback_data="action:restart"),
            InlineKeyboardButton("Мій профіль", callback_data="action:my_profile"),
        ],
        [InlineKeyboardButton("Головне меню", callback_data="action:menu")],
    ])


def _kb_skip_model() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Пропустити", callback_data="action:skip_machine")],
    ])


def _kb_unknown_brand() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Загальні поради", callback_data="action:skip_machine"),
         InlineKeyboardButton("Ввести іншу", callback_data="action:retry_model")],
    ])


def _kb_back_to_categories() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↩️ Назад до категорій", callback_data="action:back_to_categories")],
    ])


def _kb_profile() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("☕ Змінити кавомашину", callback_data="action:change_machine")],
        [
            InlineKeyboardButton("Інша проблема", callback_data="action:other_problem"),
            InlineKeyboardButton("Головне меню", callback_data="action:menu"),
        ],
    ])


async def _send_welcome(send_fn, user_id: int, username: str | None):
    repo = _get_user_repo()
    await asyncio.to_thread(repo.upsert_user, user_id, username)
    row = await asyncio.to_thread(repo.get_user, user_id) or {}
    name = row.get("name")
    machine = row.get("machine")

    state = USER_STATE.get(user_id) or {"model": None, "stage": "ready", "name": None}
    state["model"] = machine if machine else None
    state["name"] = name
    state["stage"] = "ready"
    USER_STATE[user_id] = state
    USER_CONVERSATION.pop(user_id, None)

    if not name:
        state["stage"] = "awaiting_name"
        await send_fn(
            "Привіт! Я CoffeeCare.\nЯк до вас звертатись?",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if not machine or machine == "universal":
        await send_fn(
            f"Привіт, {name}! Я CoffeeCare.\n"
            "Для точнішої відповіді — вкажіть модель кавомашини:",
            reply_markup=ReplyKeyboardRemove(),
        )
        await send_fn(
            "Оберіть, як продовжити:",
            reply_markup=_kb_step1(),
        )
    else:
        await send_fn(
            f"Привіт, {name}! Я CoffeeCare.\n"
            f"Ваша кавомашина: {_display_machine(machine)}.\nЩо сталось?",
            reply_markup=ReplyKeyboardRemove(),
        )
        await send_fn(
            "Оберіть категорію проблеми:",
            reply_markup=_kb_step2(),
        )


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await _send_welcome(
        update.message.reply_text,
        user_id,
        update.effective_user.username,
    )


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_STATE.pop(user_id, None)
    USER_MODE.pop(user_id, None)
    USER_CONVERSATION.pop(user_id, None)
    USER_MESSAGES.pop(user_id, None)
    await cmd_start(update, ctx)


async def cmd_model(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = _ensure_state(user_id)
    state["stage"] = "awaiting_model"
    await update.message.reply_text(
        "Напишіть бренд і модель вашої кавомашини. Напр.:\n"
        "  • DeLonghi Magnifica S\n"
        "  • Philips 3200 LatteGo\n"
        "  • Jura E8\n\n"
        "Або «не знаю» — відповідатиму загальними інструкціями.",
        reply_markup=_kb_skip_model(),
    )


async def cmd_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cur = USER_MODE.get(user_id, "nlp")
    new = "rule_based" if cur == "nlp" else "nlp"
    USER_MODE[user_id] = new
    await update.message.reply_text(f"Режим змінено на: {new}")


async def cmd_debug(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    arg = (ctx.args[0].lower() if ctx.args else "").strip()
    if arg in {"on", "1", "true", "yes"}:
        USER_DEBUG[user_id] = True
    elif arg in {"off", "0", "false", "no"}:
        USER_DEBUG[user_id] = False
    else:
        USER_DEBUG[user_id] = not _debug_enabled(user_id)
    state = "ON" if _debug_enabled(user_id) else "OFF"
    await update.message.reply_text(
        f"Debug mode: {state}\n(env default: DEBUG_MODE={os.getenv('DEBUG_MODE', '0')})"
    )


def _display_machine(value: str | None) -> str:
    if not value or value == "universal":
        return "не вказано"
    return value.replace("_", " ").strip().title()


def _format_profile(user_row: dict | None, state: dict) -> str:
    if not user_row:
        return "Профіль ще не створено. Надішліть кілька повідомлень — я запам'ятаю ваші вподобання."
    name = state.get("name") or user_row.get("name") or "не вказано"
    machine = _display_machine(state.get("model") or user_row.get("machine"))
    count = user_row.get("messageCount", 0)
    bio = user_row.get("bio")
    username = user_row.get("telegramUsername")

    lines = ["Ваш профіль\n"]
    lines.append(f"Ім'я: {name}")
    if username:
        lines.append(f"Telegram: @{username}")
    lines.append(f"Кавомашина: {machine}")
    lines.append(f"Повідомлень: {count}")
    if bio:
        lines.append(f"\nПро вас:\n{bio}")
    else:
        remaining = max(0, BIO_TRIGGER_AT - count)
        if remaining > 0:
            lines.append(f"\nПрофіль сформується після ще {remaining} повідомлень.")
    return "\n".join(lines)


async def _show_profile(send_fn, user_id: int, username: str | None):
    repo = _get_user_repo()
    await asyncio.to_thread(repo.upsert_user, user_id, username)
    user_row = await asyncio.to_thread(repo.get_user, user_id)
    state = _ensure_state(user_id)
    text = _format_profile(user_row, state)
    await send_fn(text, reply_markup=_kb_profile())


async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await _show_profile(update.message.reply_text, user_id, update.effective_user.username)


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_welcome(
        update.message.reply_text,
        update.effective_user.id,
        update.effective_user.username,
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>CoffeeCare — бот-консультант з кавомашин</b>\n\n"
        "Команди:\n"
        "/start — почати з початку\n"
        "/menu — головне меню\n"
        "/model — змінити кавомашину\n"
        "/profile — мій профіль\n"
        "/mode — перемкнути режим (NLP / правила)\n"
        "/reset — очистити стан\n"
        "/help — ця довідка\n\n"
        "Напишіть проблему звичайним текстом — підкажу рішення."
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_eval(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Запускаю оцінювання, зачекайте...")
    from evaluation.evaluate import run_full_evaluation

    res = run_full_evaluation()
    nlp = res["nlp"]
    rb = res["rule_based"]
    text = (
        "<b>Результати оцінювання</b>\n\n"
        f"<b>NLP (liberta-large)</b>\n"
        f"P: {nlp['precision']:.3f}  R: {nlp['recall']:.3f}  "
        f"F1: {nlp['f1']:.3f}  Top-1: {nlp['top1_accuracy']:.3f}  "
        f"Lat: {nlp['avg_latency_s']:.3f}с\n\n"
        f"<b>Rule-based</b>\n"
        f"P: {rb['precision']:.3f}  R: {rb['recall']:.3f}  "
        f"F1: {rb['f1']:.3f}  Top-1: {rb['top1_accuracy']:.3f}  "
        f"Lat: {rb['avg_latency_s']:.3f}с"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    state = _ensure_state(user_id)

    if data == "action:add_machine":
        state["stage"] = "awaiting_model"
        await query.edit_message_text(
            "Напишіть бренд і модель вашої кавомашини. Напр.:\n"
            "  • DeLonghi Magnifica S\n"
            "  • Philips 3200 LatteGo\n"
            "  • Jura E8\n\n"
            "Або «не знаю» — відповідатиму загальними інструкціями.",
            reply_markup=_kb_skip_model(),
        )

    elif data == "action:change_machine":
        state["stage"] = "awaiting_model"
        state["model"] = None
        state["hint_category"] = None
        USER_CONVERSATION.pop(user_id, None)
        await query.message.reply_text(
            "Напишіть нову модель кавомашини. Напр.:\n"
            "  • DeLonghi Magnifica S\n"
            "  • Philips 3200 LatteGo\n"
            "  • Jura E8\n\n"
            "Або «не знаю» — відповідатиму загальними інструкціями.",
            reply_markup=_kb_skip_model(),
        )

    elif data == "action:skip_machine":
        state["model"] = "universal"
        state["stage"] = "ready"
        _get_user_repo().set_machine(user_id, "universal")
        await query.edit_message_text(
            "Добре, відповідатиму загальними інструкціями.\nЩо сталось?",
            reply_markup=_kb_step2(),
        )

    elif data == "action:retry_model":
        state["stage"] = "awaiting_model"
        await query.edit_message_text(
            "Введіть марку та модель кавомашини (наприклад, Philips EP2231, DeLonghi Magnifica):",
            reply_markup=_kb_skip_model(),
        )

    elif data.startswith("category:"):
        category = data.split(":", 1)[1]
        state["hint_category"] = category
        state["stage"] = "ready"
        hints = {
            "error_code": "Напишіть код помилки або опишіть що показує дисплей.",
            "brewing": "Опишіть проблему з кавою — смак, об'єм, температура?",
            "cleaning": "Що саме потрібно: декальцинація, чистка блоку, молочна система?",
            "general": "Опишіть проблему — постараюся допомогти.",
        }
        await query.edit_message_text(
            hints.get(category, "Опишіть проблему."),
            reply_markup=_kb_back_to_categories(),
        )

    elif data == "action:back_to_categories":
        state["hint_category"] = None
        state["stage"] = "ready"
        await query.edit_message_text(
            "Оберіть категорію проблеми:",
            reply_markup=_kb_step2(),
        )

    elif data == "action:more_detail":
        conversation = USER_CONVERSATION.setdefault(user_id, {})
        last_id = conversation.get("last_entry_id")
        if last_id:
            kb = _get_assistant().kb
            entry = next((e for e in kb.entries if e.id == last_id), None)
            if entry:
                await _send_long(query.message.reply_text, entry.answer, reply_markup=_kb_post_answer())
                return
        last_chunk = conversation.get("last_chunk")
        if last_chunk and last_chunk.get("text"):
            text = last_chunk["text"]
            file = last_chunk.get("file", "")
            page = last_chunk.get("page_start")
            citation = f"\n\n_Джерело: {file}"
            if page:
                citation += f", стор. {page}"
            citation += "._"
            await _send_long(query.message.reply_text, text + citation, reply_markup=_kb_post_answer())
            return
        await query.message.reply_text(
            "Поки немає деталей до попередньої відповіді. Опишіть проблему детальніше — спробую знайти точніше рішення.",
            reply_markup=_kb_post_answer(),
        )

    elif data == "action:other_problem":
        USER_CONVERSATION.pop(user_id, None)
        state["hint_category"] = None
        await query.message.reply_text("Добре. Що ще сталося?", reply_markup=_kb_step2())

    elif data == "action:my_profile":
        await _show_profile(
            lambda text, **kw: query.message.reply_text(text, **kw),
            user_id,
            query.from_user.username,
        )
        return

    elif data == "action:menu":
        USER_CONVERSATION.pop(user_id, None)
        await _send_welcome(
            query.message.reply_text,
            user_id,
            query.from_user.username,
        )

    elif data == "action:restart":
        USER_CONVERSATION.pop(user_id, None)
        await _send_welcome(
            query.message.reply_text,
            user_id,
            query.from_user.username,
        )

    else:
        await query.message.reply_text(
            "Не зрозумів кнопку. Спробуйте /menu.",
            reply_markup=_kb_post_answer(),
        )


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""

    state = _ensure_state(user_id)
    repo = _get_user_repo()
    await asyncio.to_thread(repo.upsert_user, user_id, update.effective_user.username)

    debug_on = _debug_enabled(user_id)
    debug_steps: list[str] = []
    if debug_on:
        debug_steps.append(f"[on_message] user={user_id} text={text!r}")
        debug_steps.append(f"[state] stage={state.get('stage')} model={state.get('model')!r} name={state.get('name')!r}")

    if state.get("stage") == "awaiting_name":
        if debug_on:
            debug_steps.append("[flow] awaiting_name branch")
        name = _normalize_name(text)
        if not name:
            await update.message.reply_text(
                "Напишіть, будь ласка, ім'я (1–30 символів)."
            )
            await _send_debug(update.message.reply_text, user_id, debug_steps + ["[flow] empty name → reprompt"])
            return
        state["name"] = name
        state["stage"] = "ready"
        await asyncio.to_thread(repo.set_name, user_id, name)
        if debug_on:
            debug_steps.append(f"[repo] set_name={name!r}")
        machine = state.get("model")
        if not machine or machine == "universal":
            await update.message.reply_text(
                f"Приємно познайомитись, {name}!\n"
                "Додайте кавомашину для точніших відповідей:"
            )
            await update.message.reply_text(
                "Оберіть, як продовжити:",
                reply_markup=_kb_step1(),
            )
        else:
            await update.message.reply_text(
                f"Приємно познайомитись, {name}!\n"
                f"Ваша кавомашина: {_display_machine(machine)}.\nЩо сталось?"
            )
            await update.message.reply_text(
                "Оберіть категорію проблеми:",
                reply_markup=_kb_step2(),
            )
        await _send_debug(update.message.reply_text, user_id, debug_steps)
        return

    if state.get("stage") == "awaiting_model":
        if debug_on:
            debug_steps.append("[flow] awaiting_model branch")
        if _looks_like_skip(text):
            state["model"] = "universal"
            state["stage"] = "ready"
            await asyncio.to_thread(repo.set_machine, user_id, "universal")
            if debug_on:
                debug_steps.append("[flow] looks_like_skip → universal")
            await update.message.reply_text(
                "Добре, відповідатиму загальними інструкціями.\nЩо сталось?",
                reply_markup=_kb_step2(),
            )
            await _send_debug(update.message.reply_text, user_id, debug_steps)
            return
        if _looks_like_question(text):
            state["model"] = state.get("model") or "universal"
            state["stage"] = "ready"
            await asyncio.to_thread(repo.set_machine, user_id, state["model"])
            if debug_on:
                debug_steps.append(f"[flow] looks_like_question → model={state['model']!r}, fall through to respond")
        else:
            normalized_model = _normalize_model_input(text)
            assistant = _get_assistant()
            if (
                normalized_model != "universal"
                and assistant.known_brands
                and _resolve_brand(normalized_model, assistant.known_brands) is None
            ):
                if debug_on:
                    debug_steps.append(f"[flow] unknown brand slug={normalized_model!r} known={assistant.known_brands}")
                display = _display_machine(normalized_model)
                await update.message.reply_text(
                    f"Не знайшов інструкцій для «{display}». "
                    "Можу відповідати загальними порадами, або введіть іншу модель "
                    "(наприклад, Philips EP2231, DeLonghi Magnifica).",
                    reply_markup=_kb_unknown_brand(),
                )
                await _send_debug(update.message.reply_text, user_id, debug_steps)
                return
            state["model"] = normalized_model
            state["stage"] = "ready"
            await asyncio.to_thread(repo.set_machine, user_id, normalized_model)
            if debug_on:
                debug_steps.append(f"[repo] set_machine={normalized_model!r}")
            label = _display_machine(normalized_model) if normalized_model != "universal" else None
            reply = (
                f"Записав: {label}.\n\nТепер — що сталося?" if label
                else "Добре. Що сталося з машиною?"
            )
            await update.message.reply_text(reply, reply_markup=_kb_step2())
            await _send_debug(update.message.reply_text, user_id, debug_steps)
            return

    model = state.get("model") or "universal"
    mode = USER_MODE.get(user_id, "nlp")
    conversation = USER_CONVERSATION.setdefault(user_id, {})

    buf = USER_MESSAGES.setdefault(user_id, [])
    if len(buf) < BIO_TRIGGER_AT:
        buf.append(text)
    count = await asyncio.to_thread(repo.increment_message_count, user_id)
    if debug_on:
        debug_steps.append(f"[repo] increment_message_count → {count}")

    user_row = await asyncio.to_thread(repo.get_user, user_id)
    user_bio = user_row.get("bio") if user_row else None
    if not state.get("name") and user_row and user_row.get("name"):
        state["name"] = user_row["name"]
    if not state.get("model") and user_row and user_row.get("machine"):
        state["model"] = user_row["machine"]
        model = state["model"]
    if debug_on:
        debug_steps.append(f"[merge] name={state.get('name')!r} model={model!r} bio={'yes' if user_bio else 'no'}")

    if (
        count is not None
        and count >= BIO_TRIGGER_AT
        and user_bio is None
        and len(buf) >= BIO_TRIGGER_AT
    ):
        if debug_on:
            debug_steps.append(f"[bio] generating (count={count} buf={len(buf)})")
        bio = await asyncio.to_thread(lambda: generate_bio(buf, machine=model))
        if bio:
            await asyncio.to_thread(repo.set_bio, user_id, bio)
            user_bio = bio
            if debug_on:
                debug_steps.append(f"[bio] saved len={len(bio)}")
        elif debug_on:
            debug_steps.append("[bio] generation returned empty")

    if debug_on:
        debug_steps.append(f"[dispatch] mode={mode}")

    pump_task: asyncio.Task | None = None
    started_at = time.monotonic()
    if debug_on:
        pump_task = asyncio.create_task(
            _live_debug_pump(ctx.bot, update.effective_chat.id, debug_steps, started_at)
        )

    respond_failed = False
    try:
        if mode == "rule_based":
            result = await asyncio.to_thread(_get_rule_based().respond, text)
            if debug_on:
                debug_steps.append(f"[rule_based] source={result.get('source')} cat={result.get('category')} conf={result.get('confidence')}")
        else:
            result = await asyncio.to_thread(
                _get_assistant().respond,
                text,
                state.get("name"),
                model,
                conversation,
                user_bio,
                debug_steps if debug_on else None,
            )
    except Exception as e:
        log.exception("respond() failed for user %s: %s", user_id, e)
        if debug_on:
            debug_steps.append(f"[error] {type(e).__name__}: {e}")
        result = {
            "response": "Не вдалося обробити запит. Спробуйте перефразувати або /reset.",
            "source": "error",
            "category": None,
            "confidence": 0.0,
            "kb_entry_id": None,
        }
        respond_failed = True
    finally:
        if pump_task is not None:
            pump_task.cancel()
            try:
                await pump_task
            except asyncio.CancelledError:
                pass

    if debug_on:
        debug_steps.append(f"[result] +{time.monotonic() - started_at:.2f}s source={result.get('source')} cat={result.get('category')} conf={result.get('confidence')} kb={result.get('kb_entry_id')}")

    await _send_long(update.message.reply_text, result["response"], reply_markup=_kb_post_answer())
    await _send_debug(update.message.reply_text, user_id, debug_steps)


_SKIP_TOKENS = {
    "не знаю", "не знаю.", "не пам'ятаю", "не пам’ятаю",
    "немає", "нема", "хз", "skip", "пропустити", "пропустити.",
    "—", "-", "—.", "?", "??",
}


def _looks_like_skip(text: str) -> bool:
    return text.strip().lower() in _SKIP_TOKENS


def _looks_like_question(text: str) -> bool:
    t = text.strip().lower()
    if "?" in t:
        return True
    return any(t.startswith(w) for w in ("що ", "як ", "чому ", "де ", "коли ", "чи "))


def _normalize_name(text: str) -> str | None:
    cleaned = text.strip()
    if not cleaned:
        return None
    cleaned = cleaned.replace("\n", " ")
    if len(cleaned) > 30:
        cleaned = cleaned[:30].rstrip()
    return cleaned or None


def _normalize_model_input(text: str) -> str:
    cleaned = text.strip().lower()
    if not cleaned or cleaned in {"не знаю", "не знаю.", "?", "хз", "skip", "пропустити"}:
        return "universal"
    import re
    slug = re.sub(r"[^\w\s]", "", cleaned)
    slug = re.sub(r"\s+", "_", slug.strip())
    return slug[:60] or "universal"


def start_bot():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or token == "your_token_here":
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing or placeholder in .env")

    app = Application.builder().token(token).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("mode", cmd_mode))
    app.add_handler(CommandHandler("debug", cmd_debug))
    app.add_handler(CommandHandler("eval", cmd_eval))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)

    log.info("Bot starting... DEBUG_MODE=%s", os.getenv("DEBUG_MODE", "0"))
    threading.Thread(target=_warmup, daemon=True, name="warmup").start()
    app.run_polling()


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    err = ctx.error
    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__)) if err else ""
    log.error("Unhandled exception in handler: %s\n%s", err, tb)
    chat_id = None
    if isinstance(update, Update):
        if update.effective_chat:
            chat_id = update.effective_chat.id
    if chat_id is None:
        return
    try:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text="Сталася технічна помилка. Спробуйте ще раз або /reset.",
            reply_markup=_kb_post_answer(),
        )
    except Exception as e:
        log.warning("Failed to notify user about error: %s", e)


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "Почати"),
        BotCommand("menu", "Головне меню"),
        BotCommand("model", "Змінити кавомашину"),
        BotCommand("profile", "Мій профіль"),
        BotCommand("mode", "Перемкнути режим"),
        BotCommand("reset", "Очистити стан"),
        BotCommand("help", "Довідка"),
    ])


def _warmup():
    t0 = time.monotonic()
    try:
        log.info("[warmup] loading classifier (liberta) + assistant...")
        assistant = _get_assistant()
        log.info("[warmup] classifier ready (+%.2fs). Pinging Qdrant...", time.monotonic() - t0)
        if assistant.retriever is not None:
            try:
                assistant.retriever.search_qa("warmup", model=None, k=1)
                if assistant.known_brands:
                    assistant.retriever.search_chunks("warmup", model=None, k=1)
                log.info("[warmup] Qdrant warm (+%.2fs).", time.monotonic() - t0)
            except Exception as e:
                log.warning("[warmup] Qdrant ping failed: %s", e)
        log.info("[warmup] pinging Lapa...")
        try:
            assistant.generator.generate(
                user_query="тест",
                retrieved_instruction="тестова інструкція для прогріву моделі.",
                category="warmup",
            )
            log.info("[warmup] Lapa hot (+%.2fs total).", time.monotonic() - t0)
        except Exception as e:
            log.warning("[warmup] Lapa ping failed: %s", e)
        log.info("[warmup] complete (+%.2fs).", time.monotonic() - t0)
    except Exception as e:
        log.warning("[warmup] failed: %s", e)


if __name__ == "__main__":
    start_bot()
