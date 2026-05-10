"""
Telegram bot for CoffeeCare.

Commands:
  /start  — greet and start flow
  /model  — set machine brand/model
  /mode   — toggle NLP <-> rule_based per user
  /reset  — clear user state
  /eval   — dev only: run evaluation
"""

import os
import logging

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from src.knowledge_base import KnowledgeBase
from src.assistant import CoffeeBotAssistant
from src.rule_based import RuleBasedAssistant
from src.user_repository import UserRepository
from src.bio_generator import generate_bio

BIO_TRIGGER_AT = 5

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

USER_STATE: dict[int, dict] = {}
USER_MODE: dict[int, str] = {}
USER_CONVERSATION: dict[int, dict] = {}
USER_MESSAGES: dict[int, list[str]] = {}

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
        _rule_based = RuleBasedAssistant(KnowledgeBase())
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
        [
            InlineKeyboardButton("Додати кавомашину ☕", callback_data="action:add_machine"),
            InlineKeyboardButton("Пропустити", callback_data="action:skip_machine"),
        ]
    ])


def _kb_step2() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Помилка на дисплеї", callback_data="category:error_code"),
            InlineKeyboardButton("Проблема з кавою", callback_data="category:brewing"),
        ],
        [
            InlineKeyboardButton("Обслуговування / чистка", callback_data="category:cleaning"),
            InlineKeyboardButton("Інше", callback_data="category:general"),
        ],
    ])


def _kb_post_answer() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Детальніше", callback_data="action:more_detail"),
            InlineKeyboardButton("Інша проблема", callback_data="action:other_problem"),
            InlineKeyboardButton("Почати знову", callback_data="action:restart"),
        ],
        [
            InlineKeyboardButton("Мій профіль", callback_data="action:my_profile"),
        ],
    ])


async def _send_welcome(send_fn, user_id: int, username: str | None):
    repo = _get_user_repo()
    repo.upsert_user(user_id, username)
    row = repo.get_user(user_id) or {}
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
        )
        return

    if not machine or machine == "universal":
        await send_fn(
            f"Привіт, {name}! Я CoffeeCare.\n"
            "Для точнішої відповіді — вкажіть модель кавомашини:",
            reply_markup=_kb_step1(),
        )
    else:
        await send_fn(
            f"Привіт, {name}! Я CoffeeCare.\n"
            f"Ваша кавомашина: {machine}.\nЩо сталось?",
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
    )


async def cmd_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cur = USER_MODE.get(user_id, "nlp")
    new = "rule_based" if cur == "nlp" else "nlp"
    USER_MODE[user_id] = new
    await update.message.reply_text(f"Режим змінено на: {new}")


def _format_profile(user_row: dict | None, state: dict) -> str:
    if not user_row:
        return "Профіль ще не створено. Надішліть кілька повідомлень — я запам'ятаю ваші вподобання."
    name = state.get("name") or user_row.get("name") or "не вказано"
    machine = state.get("model") or user_row.get("machine") or "не вказано"
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
    repo.upsert_user(user_id, username)
    user_row = repo.get_user(user_id)
    state = _ensure_state(user_id)
    text = _format_profile(user_row, state)
    await send_fn(text, reply_markup=_kb_post_answer())


async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await _show_profile(update.message.reply_text, user_id, update.effective_user.username)


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
        )

    elif data == "action:skip_machine":
        state["model"] = state.get("model") or "universal"
        state["stage"] = "ready"
        await query.edit_message_text(
            "Добре. Що сталося з машиною?",
            reply_markup=_kb_step2(),
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
        await query.edit_message_text(hints.get(category, "Опишіть проблему."))

    elif data == "action:more_detail":
        conversation = USER_CONVERSATION.setdefault(user_id, {})
        last_id = conversation.get("last_entry_id")
        if last_id:
            kb = _get_assistant().kb
            entry = next((e for e in kb.entries if e.id == last_id), None)
            if entry:
                await query.message.reply_text(entry.answer, reply_markup=_kb_post_answer())
                return
        await query.message.reply_text("Опишіть проблему детальніше — спробую знайти точнішу відповідь.")

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

    elif data == "action:restart":
        USER_CONVERSATION.pop(user_id, None)
        await _send_welcome(
            query.message.reply_text,
            user_id,
            query.from_user.username,
        )


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""

    state = _ensure_state(user_id)
    repo = _get_user_repo()
    repo.upsert_user(user_id, update.effective_user.username)

    if state.get("stage") == "awaiting_name":
        name = _normalize_name(text)
        if not name:
            await update.message.reply_text("Напишіть, будь ласка, ім'я (1–30 символів).")
            return
        state["name"] = name
        state["stage"] = "ready"
        repo.set_name(user_id, name)
        machine = state.get("model")
        if not machine or machine == "universal":
            await update.message.reply_text(
                f"Приємно познайомитись, {name}!\n"
                "Додайте кавомашину для точніших відповідей:",
                reply_markup=_kb_step1(),
            )
        else:
            await update.message.reply_text(
                f"Приємно познайомитись, {name}!\n"
                f"Ваша кавомашина: {machine}.\nЩо сталось?",
                reply_markup=_kb_step2(),
            )
        return

    if state.get("stage") == "awaiting_model":
        if _looks_like_question(text):
            state["stage"] = "ready"
            state["model"] = state.get("model") or "universal"
        else:
            normalized_model = _normalize_model_input(text)
            state["model"] = normalized_model
            state["stage"] = "ready"
            repo.set_machine(user_id, normalized_model)
            label = normalized_model if normalized_model != "universal" else None
            reply = (
                f"Записав: {label}.\n\nТепер — що сталося?" if label
                else "Добре. Що сталося з машиною?"
            )
            await update.message.reply_text(reply, reply_markup=_kb_step2())
            return

    model = state.get("model") or "universal"
    mode = USER_MODE.get(user_id, "nlp")
    conversation = USER_CONVERSATION.setdefault(user_id, {})

    buf = USER_MESSAGES.setdefault(user_id, [])
    if len(buf) < BIO_TRIGGER_AT:
        buf.append(text)
    count = repo.increment_message_count(user_id)

    user_row = repo.get_user(user_id)
    user_bio = user_row.get("bio") if user_row else None
    if not state.get("name") and user_row and user_row.get("name"):
        state["name"] = user_row["name"]
    if not state.get("model") and user_row and user_row.get("machine"):
        state["model"] = user_row["machine"]
        model = state["model"]

    if (
        count is not None
        and count >= BIO_TRIGGER_AT
        and user_bio is None
        and len(buf) >= BIO_TRIGGER_AT
    ):
        bio = generate_bio(buf, machine=model)
        if bio:
            repo.set_bio(user_id, bio)
            user_bio = bio

    if mode == "rule_based":
        result = _get_rule_based().respond(text)
    else:
        result = _get_assistant().respond(
            text,
            user_name=state.get("name"),
            machine_model=model,
            conversation=conversation,
            user_bio=user_bio,
        )

    await update.message.reply_text(result["response"], reply_markup=_kb_post_answer())


def _looks_like_question(text: str) -> bool:
    t = text.strip().lower()
    if "?" in t or len(t) > 25:
        return True
    return any(t.startswith(w) for w in ("що ", "як ", "чому ", "де ", "коли ", "чи ", "не "))


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

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("mode", cmd_mode))
    app.add_handler(CommandHandler("eval", cmd_eval))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    start_bot()
