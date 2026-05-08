"""
Telegram bot for CoffeeBot.

Commands:
  /start  — greet and show keyboard
  /help   — usage hint
  /model  — set machine brand/model
  /mode   — toggle NLP <-> rule_based per user
  /reset  — clear user state
  /eval   — dev only: run evaluation
"""

import os
import logging

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


BTN_MACHINE = "☕ Моя машина ☕"
BTN_ERRORS = "⚠️ Коди помилок ⚠️"
BTN_TIPS = "💡 Поради 💡"
BTN_HELP = "❓ Допомога ❓"
BTN_RESET = "🔄 Скинути 🔄"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [BTN_MACHINE, BTN_TIPS],
        [BTN_ERRORS, BTN_HELP],
        [BTN_RESET],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

from src.knowledge_base import KnowledgeBase
from src.assistant import CoffeeBotAssistant
from src.rule_based import RuleBasedAssistant

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

USER_STATE: dict[int, dict] = {}
USER_MODE: dict[int, str] = {}
USER_CONVERSATION: dict[int, dict] = {}

_assistant: CoffeeBotAssistant | None = None
_rule_based: RuleBasedAssistant | None = None


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


def _ensure_state(user_id: int) -> dict:
    state = USER_STATE.get(user_id)
    if state is None:
        state = {"model": None, "stage": "ready"}
        USER_STATE[user_id] = state
    return state


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_STATE[user_id] = {"model": None, "stage": "ready"}
    await update.message.reply_text(
        "Привіт! Я CoffeeBot ☕\n"
        "Допоможу з кавомашиною — поясню коди помилок, дам поради по обслуговуванню та діагностиці.\n\n"
        "Опишіть проблему звичайною мовою або скористайтесь кнопками нижче.",
        reply_markup=MAIN_KEYBOARD,
    )


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_STATE.pop(user_id, None)
    USER_MODE.pop(user_id, None)
    USER_CONVERSATION.pop(user_id, None)
    await cmd_start(update, ctx)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ Як користуватися\n\n"
        "Опишіть проблему звичайною мовою, наприклад:\n"
        "  • «Машина не вмикається»\n"
        "  • «Що означає E03?»\n"
        "  • «Кава дуже водяниста»\n\n"
        "Або натисніть кнопку нижче.",
        reply_markup=MAIN_KEYBOARD,
    )


async def cmd_tips(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💡 Загальні поради:\n\n"
        "• Чистіть заварний блок раз на тиждень\n"
        "• Декальцинуйте кожні 2-3 місяці\n"
        "• Використовуйте фільтровану воду\n"
        "• Зберігайте зерно в герметичній тарі\n"
        "• Помел: дрібніший → міцніше; крупніший → водянисто\n\n"
        "Опишіть конкретну ситуацію — дам точну пораду.",
        reply_markup=MAIN_KEYBOARD,
    )


async def cmd_errors(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ Поширені коди помилок:\n\n"
        "• E01 / Err1 — проблема з гідросистемою\n"
        "• E03 — перегрів термоблоку\n"
        "• E04 — заклинило заварний блок\n"
        "• E05 — датчик потоку води\n"
        "• Calc / Decalc — потрібна декальцинація\n\n"
        "Напишіть код вашої помилки — поясню деталі.",
        reply_markup=MAIN_KEYBOARD,
    )


async def cmd_model(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = _ensure_state(user_id)
    state["stage"] = "awaiting_model"
    await update.message.reply_text(
        "☕ Напишіть бренд і модель вашої кавомашини. Напр.:\n"
        "  • DeLonghi Magnifica S\n"
        "  • Philips 3200 LatteGo\n"
        "  • Jura E8\n\n"
        "Або «не знаю» — буду відповідати загальними інструкціями.",
        reply_markup=MAIN_KEYBOARD,
    )


async def cmd_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cur = USER_MODE.get(user_id, "nlp")
    new = "rule_based" if cur == "nlp" else "nlp"
    USER_MODE[user_id] = new
    await update.message.reply_text(f"Режим змінено на: {new}")


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


BUTTON_HANDLERS = {
    BTN_MACHINE: cmd_model,
    BTN_ERRORS: cmd_errors,
    BTN_TIPS: cmd_tips,
    BTN_HELP: cmd_help,
    BTN_RESET: cmd_reset,
}


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""

    handler = BUTTON_HANDLERS.get(text.strip())
    if handler is not None:
        await handler(update, ctx)
        return

    state = _ensure_state(user_id)

    if state.get("stage") == "awaiting_model":
        if _looks_like_question(text):
            state["stage"] = "ready"
            state["model"] = state.get("model") or "universal"
        else:
            normalized_model = _normalize_model_input(text)
            state["model"] = normalized_model
            state["stage"] = "ready"
            if normalized_model == "universal":
                await update.message.reply_text(
                    "Без проблем — буду відповідати загальними порадами.\n\n"
                    "Розкажіть, що сталося з машиною.",
                    reply_markup=MAIN_KEYBOARD,
                )
            else:
                await update.message.reply_text(
                    f"Записав: {normalized_model}.\n\n"
                    "Тепер розкажіть, що сталося з машиною — симптом, помилку або проблему.",
                    reply_markup=MAIN_KEYBOARD,
                )
            return

    model = state.get("model") or "universal"
    mode = USER_MODE.get(user_id, "nlp")
    conversation = USER_CONVERSATION.setdefault(user_id, {})

    if mode == "rule_based":
        result = _get_rule_based().respond(text)
    else:
        result = _get_assistant().respond(
            text, user_name=None, machine_model=model, conversation=conversation
        )

    await update.message.reply_text(result["response"], reply_markup=MAIN_KEYBOARD)


def _looks_like_question(text: str) -> bool:
    t = text.strip().lower()
    if "?" in t or len(t) > 50:
        return True
    return any(t.startswith(w) for w in ("що ", "як ", "чому ", "де ", "коли ", "чи ", "не "))


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
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("mode", cmd_mode))
    app.add_handler(CommandHandler("eval", cmd_eval))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    start_bot()
