"""
Generate a short user bio from first conversation messages via Lapa LLM (Ollama).
"""

import logging

import requests

from src.generator import LLAMA_MODEL, OLLAMA_URL

log = logging.getLogger(__name__)

BIO_SYSTEM_PROMPT = """Ти — асистент, що складає короткий профіль користувача кавомашини.
На основі перших повідомлень користувача напиши 2–3 речення українською мовою про:
- рівень досвіду (новачок, досвідчений, професіонал),
- модель кавомашини (якщо згадана),
- основні проблеми або інтереси.
Без списків, без заголовків, без емодзі, без вигадок. Тільки факти з повідомлень."""


def _build_prompt(messages: list[str], machine: str | None) -> str:
    machine_line = f"Кавомашина користувача: {machine}.\n" if machine and machine != "universal" else ""
    joined = "\n".join(f"- {m}" for m in messages)
    return f"""{BIO_SYSTEM_PROMPT}

{machine_line}Перші повідомлення користувача:
{joined}

Профіль (2–3 речення):"""


def generate_bio(
    messages: list[str],
    machine: str | None = None,
    ollama_url: str = OLLAMA_URL,
    model: str = LLAMA_MODEL,
    timeout: int = 30,
) -> str | None:
    if not messages:
        return None
    prompt = _build_prompt(messages, machine)
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.4,
            "num_predict": 160,
            "top_p": 0.9,
            "num_ctx": 1024,
        },
    }
    try:
        response = requests.post(ollama_url, json=payload, timeout=timeout)
        response.raise_for_status()
        bio = response.json().get("response", "").strip()
        return bio or None
    except Exception as exc:
        log.warning(f"[bio_generator] Lapa LLM error: {exc}")
        return None
