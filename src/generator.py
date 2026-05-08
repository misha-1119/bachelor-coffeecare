"""
Stage 2: LLaMA 2 via Ollama for generating short conversational responses.

The generator turns a long KB instruction into a short human-like reply
addressed to the user by name, never copying the KB text verbatim.
"""

import os
import time

import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
LLAMA_MODEL = os.getenv("LLAMA_MODEL", "hf.co/lapa-llm/lapa-v0.1.2-instruct-GGUF")

SYSTEM_PROMPT = """Ти — CoffeeBot, дружній помічник з проблемами кавомашин. Відповідаєш як друг, а не як інструкція.

Стиль обов'язковий:
- Лише українська мова, природна, розмовна.
- Дуже КОРОТКО: 2–4 речення, максимум 5.
- НЕ використовуй заголовки, нумеровані списки довші 3 пунктів, маркери з зірочок чи стрілок, емодзі.
- НЕ копіюй текст інструкції дослівно — переказуй своїми словами найважливіше (1–2 кроки).
- Якщо в користувача є ім'я — звертайся до нього на ім'я один раз (на початку або в середині).
- Закінчуй короткою фразою, що залишає діалог відкритим: «Допомогло?», «Розказати докладніше?», «Що показує машина зараз?».
- НЕ вигадуй фактів, моделей, кнопок чи цифр поза інструкцією.
- НЕ повторюй питання користувача.
"""


def build_prompt(user_query: str, retrieved_instruction: str, category: str, user_name: str | None = None) -> str:
    name_line = f"Користувача звати {user_name}. Звертайся на ім'я один раз.\n" if user_name else ""
    return f"""{SYSTEM_PROMPT}
{name_line}
Категорія проблеми: {category}
Запит користувача: {user_query}

Інструкція з бази знань (єдине джерело правди, перекажи коротко):
\"\"\"
{retrieved_instruction}
\"\"\"

Дай коротку (2–4 речення) розмовну відповідь українською, без списків і заголовків. Закінчи відкритим питанням."""


class Generator:
    def __init__(self, model: str = LLAMA_MODEL, ollama_url: str = OLLAMA_URL):
        self.model = model
        self.ollama_url = ollama_url
        self._available_cache: tuple[float, bool] | None = None
        self._cache_ttl = 60.0

    def _is_available(self) -> bool:
        if os.getenv("DISABLE_LLAMA") == "1":
            return False
        now = time.time()
        if self._available_cache and now - self._available_cache[0] < self._cache_ttl:
            return self._available_cache[1]
        try:
            r = requests.get("http://localhost:11434/api/tags", timeout=2)
            ok = r.status_code == 200
        except Exception:
            ok = False
        self._available_cache = (now, ok)
        return ok

    def generate(
        self,
        user_query: str,
        retrieved_instruction: str,
        category: str,
        user_name: str | None = None,
    ) -> str:
        if not self._is_available():
            return self._fallback(retrieved_instruction, user_name)

        prompt = build_prompt(user_query, retrieved_instruction, category, user_name)
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 160,
                "top_p": 0.9,
                "num_ctx": 1024,
            },
        }
        try:
            response = requests.post(self.ollama_url, json=payload, timeout=30)
            response.raise_for_status()
            return response.json().get("response", "").strip()
        except Exception as e:
            print(f"[Generator] LLaMA 2 error: {e}")
            return self._fallback(retrieved_instruction, user_name)

    def _fallback(self, instruction: str, user_name: str | None = None) -> str:
        first = instruction.split("\n\n")[0].strip()
        if len(first) > 320:
            first = first[:300].rsplit(" ", 1)[0] + "..."
        addr = f"{user_name}, " if user_name else ""
        return f"{addr}{first}\n\nНапишіть «детальніше» — поясню крок за кроком."
