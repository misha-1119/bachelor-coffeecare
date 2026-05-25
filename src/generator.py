"""
Stage 2: LLM for generating short conversational responses.

Priority order:
1. Groq API (llama-3.3-70b-versatile) — fast cloud inference, ~300 tok/s
2. Lapa via local Ollama — Ukrainian-specialised, slow on CPU
3. KB text fallback — always available, no LLM needed
"""

import os
import time

import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
LLAMA_MODEL = os.getenv("LLAMA_MODEL", "hf.co/lapa-llm/lapa-v0.1.2-instruct-GGUF")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

SYSTEM_PROMPT = """Ти — CoffeeBot, дружній помічник з проблемами кавомашин. Відповідаєш як друг, а не як інструкція.

Стиль обов'язковий:
- Лише українська мова, природна, розмовна.
- Дуже КОРОТКО: 2–4 речення, максимум 5.
- НЕ використовуй заголовки, нумеровані списки довші 3 пунктів, маркери з зірочок чи стрілок, емодзі.
- НЕ копіюй текст інструкції дослівно — переказуй своїми словами найважливіше (1–2 кроки).
- Якщо в повідомленні вказано ім'я користувача — звертайся до нього один раз. Якщо імені немає — НЕ вигадуй його і не використовуй жодних імен.
- Закінчуй короткою фразою, що залишає діалог відкритим: «Допомогло?», «Розказати докладніше?», «Що показує машина зараз?».
- НЕ вигадуй фактів, моделей, кнопок чи цифр поза інструкцією.
- НЕ повторюй питання користувача.
"""


def build_user_message(
    user_query: str,
    retrieved_instruction: str,
    category: str,
    user_name: str | None = None,
    user_bio: str | None = None,
) -> str:
    name_line = f"Користувача звати {user_name}. Звертайся на ім'я один раз.\n" if user_name else ""
    bio_line = f"Контекст користувача: {user_bio}\n" if user_bio else ""
    if category == "manual":
        source_label = "Уривок з PDF-мануалу кавомашини (єдине джерело правди — переказуй лише те, що там написано)"
        tail = (
            "Перекажи 2–4 коротких кроки з уривка природною українською, без копіювання дослівно. "
            "Якщо в уривку немає прямої відповіді на запит — скажи це чесно і запропонуй описати симптом докладніше. "
            "Закінчи відкритим питанням."
        )
    else:
        source_label = "Інструкція з бази знань (єдине джерело правди, перекажи коротко)"
        tail = "Дай коротку (2–4 речення) розмовну відповідь українською, без списків і заголовків. Закінчи відкритим питанням."

    return (
        f"{name_line}{bio_line}"
        f"Категорія проблеми: {category}\n"
        f"Запит користувача: {user_query}\n\n"
        f"{source_label}:\n"
        f'"""\n{retrieved_instruction}\n"""\n\n'
        f"{tail}"
    )


# Keep for Ollama (single-string prompt format)
def build_prompt(
    user_query: str,
    retrieved_instruction: str,
    category: str,
    user_name: str | None = None,
    user_bio: str | None = None,
) -> str:
    return f"{SYSTEM_PROMPT}\n{build_user_message(user_query, retrieved_instruction, category, user_name, user_bio)}"


class Generator:
    def __init__(self, model: str = LLAMA_MODEL, ollama_url: str = OLLAMA_URL):
        self.model = model
        self.ollama_url = ollama_url
        self._ollama_cache: tuple[float, bool] | None = None
        self._cache_ttl = 60.0

    # ── availability ──────────────────────────────────────────────────────────

    def _groq_available(self) -> bool:
        return bool(os.getenv("GROQ_API_KEY")) and os.getenv("DISABLE_GROQ") != "1"

    def _ollama_available(self) -> bool:
        if os.getenv("DISABLE_LLAMA") == "1":
            return False
        now = time.time()
        if self._ollama_cache and now - self._ollama_cache[0] < self._cache_ttl:
            return self._ollama_cache[1]
        try:
            r = requests.get("http://localhost:11434/api/tags", timeout=2)
            ok = r.status_code == 200
        except Exception:
            ok = False
        self._ollama_cache = (now, ok)
        return ok

    def _is_available(self) -> bool:
        return self._groq_available() or self._ollama_available()

    # ── backends ──────────────────────────────────────────────────────────────

    def _generate_groq(self, user_msg: str) -> str:
        api_key = os.getenv("GROQ_API_KEY")
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": 200,
            "temperature": 0.3,
            "top_p": 0.9,
        }
        r = requests.post(
            GROQ_API_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()

    def _generate_ollama(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": "10m",
            "options": {
                "temperature": 0.3,
                "num_predict": 80,
                "top_p": 0.9,
                "num_ctx": 1024,
            },
        }
        r = requests.post(self.ollama_url, json=payload, timeout=90)
        r.raise_for_status()
        return r.json().get("response", "").strip()

    # ── public ────────────────────────────────────────────────────────────────

    def generate(
        self,
        user_query: str,
        retrieved_instruction: str,
        category: str,
        user_name: str | None = None,
        user_bio: str | None = None,
    ) -> str:
        if not self._is_available():
            return self._fallback(retrieved_instruction, user_name)

        user_msg = build_user_message(user_query, retrieved_instruction, category, user_name, user_bio)

        if self._groq_available():
            try:
                return self._generate_groq(user_msg)
            except Exception as e:
                print(f"[Generator] Groq error: {e}")

        if self._ollama_available():
            try:
                return self._generate_ollama(f"{SYSTEM_PROMPT}\n{user_msg}")
            except Exception as e:
                print(f"[Generator] Lapa LLM error: {e}")

        return self._fallback(retrieved_instruction, user_name)

    def _fallback(self, instruction: str, user_name: str | None = None) -> str:
        first = instruction.split("\n\n")[0].strip()
        if len(first) > 320:
            first = first[:300].rsplit(" ", 1)[0] + "..."
        addr = f"{user_name}, " if user_name else ""
        return f"{addr}{first}\n\nНапишіть «детальніше» — поясню крок за кроком."
