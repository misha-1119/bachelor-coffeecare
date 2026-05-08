"""
Triage and input normalization.

Two responsibilities:
1. Normalize colloquial / typo-laden Ukrainian input before classification.
2. Detect greetings (short conversational replies handled directly).

The original 'needs_clarification' rule has been moved into the KB itself —
vague queries now match dedicated 'clarify' KB entries whose answers are
clarifying questions. This keeps triage simple and lets the classifier do
its job.
"""

import re

GREET_RE = re.compile(
    r"^(привіт|приветствую|здоров|добрий\s+(день|ранок|вечір)|добридень|добрий|hi|hello|hey)\b",
    re.IGNORECASE,
)

GOODBYE_RE = re.compile(
    r"^(дякую|спасибі|до\s+побачення|па|бувайте|thanks|thank\s+you|bye)\b",
    re.IGNORECASE,
)

FOLLOWUP_YES_RE = re.compile(
    r"^(так|да|yes|ага|угу|ok|окей|ок|допомогло|працює|спрацювало|вирішив(ся|ось|сь)?|fixed)\b",
    re.IGNORECASE,
)

FOLLOWUP_NO_RE = re.compile(
    r"^(ні+|нє+|нєєє|нєт|не-а|неа|нєа|nope|нет|no+|"
    r"не\s+(допомогло|спрацювало|працює|допомагає)|"
    r"так\s+само|той\s+самий|same|still)\b",
    re.IGNORECASE,
)

NEGATIVE_META_RE = re.compile(
    r"(не\s+в\s+цьому|не\s+те|не\s+правильно|неправильн|"
    r"як(і|их)\s+(є\s+)?(ще|інші)\s+варіант|інше\s+рішенн|"
    r"ти\s+не\s+допоміг|не\s+допоміг\s+мен|не\s+допомагаєш|"
    r"шо\s+ти\s+(говориш|кажеш|пишеш)|що\s+ти\s+(говориш|кажеш|пишеш)|"
    r"не\s+зрозум|не\s+про\s+те|маячня|нісенітниц|туп(иш|іш))",
    re.IGNORECASE,
)

MORE_DETAIL_RE = re.compile(
    r"^(детальніше|докладніше|розкажи?\s+(детальн|докладн|більше|ще)|"
    r"поясни\s+(краще|детальн|докладн)|ще\s+(інфо|варіант)|"
    r"продовж|давай\s+далі|більше\s+інфо)",
    re.IGNORECASE,
)

NORMALIZATION_MAP: dict[str, str] = {
    "шось": "щось",
    "шо ": "що ",
    "шо?": "що?",
    "ніт": "нема",
    "нема́": "нема",
    "нічо": "нічого",
    "не работает": "не працює",
    "ничего": "нічого",
    "светицця": "світиться",
    "светится": "світиться",
    "свіиться": "світиться",
    "горит": "горить",
    "мигает": "блимає",
    "моргає": "блимає",
    "мигае": "блимає",
    "не фурычит": "не працює",
    "не фурычить": "не працює",
    "помилкка": "помилка",
    "помика": "помилка",
    "ошибка": "помилка",
    "сламалась": "зламалась",
    "сломалась": "зламалась",
    "поломалась": "зламалась",
    "проблеми": "проблема",
    "красным": "червоним",
    "красное": "червоне",
    "красный": "червоний",
    "пар не йде": "пар не йде",
    "помол не идет": "помол не йде",
    "тече вода": "тече вода",
    "капает": "капає",
    "капле": "капає",
    "проте́кає": "протікає",
    "протекает": "протікає",
    "хочи": "хочу",
    "почистити": "почистити",
    "пачистити": "почистити",
    "чищенье": "чищення",
    "помойка": "контейнер",
    "посуду": "посуд",
    "вкл": "увімкнути",
    "выкл": "вимкнути",
    "не включается": "не вмикається",
    "не выключается": "не вимикається",
    "горяча": "гаряча",
    "горячий": "гарячий",
    "холодная": "холодна",
}


URGENT_TRIGGERS_RE = re.compile(
    r"\b(дим|пожеж|іскр|удар\s+струм|пахне\s+пал|горить\s+пластмас|плавиться)",
    re.IGNORECASE,
)


def normalize(query: str) -> str:
    """Lowercase fix common typos / colloquial spellings."""
    q = query.strip()
    if not q:
        return q
    low = q.lower()
    for bad, good in NORMALIZATION_MAP.items():
        low = low.replace(bad, good)
    return low


def is_greeting(query: str) -> bool:
    return bool(GREET_RE.match(query.strip()))


def is_goodbye(query: str) -> bool:
    return bool(GOODBYE_RE.match(query.strip()))


def is_urgent_safety(query: str) -> bool:
    """Detect dangerous symptoms — dim, fire, electric shock, burning plastic."""
    return bool(URGENT_TRIGGERS_RE.search(query))


def is_followup_yes(query: str) -> bool:
    return bool(FOLLOWUP_YES_RE.match(query.strip()))


def is_followup_no(query: str) -> bool:
    return bool(FOLLOWUP_NO_RE.match(query.strip()))


def is_negative_meta(query: str) -> bool:
    return bool(NEGATIVE_META_RE.search(query))


def is_more_detail(query: str) -> bool:
    return bool(MORE_DETAIL_RE.match(query.strip()))


def negative_meta_reply(name: str | None = None) -> str:
    addr = f"{name}, " if name else ""
    return (
        f"{addr}вибачте, схоже не в той бік. Опишіть конкретніше:\n"
        "• який код помилки на дисплеї (якщо є)?\n"
        "• що саме сталося — кава не йде, тече вода, дивний звук, не вмикається?\n"
        "• після чого з'явилось — після чищення, заміни зерна, нічого не робили?"
    )


def greeting_reply(name: str | None = None) -> str:
    if name:
        return f"Привіт, {name}! Розкажіть, що з кавомашиною — допоможу розібратися."
    return "Привіт! Розкажіть, що з кавомашиною — допоможу розібратися."


def goodbye_reply(name: str | None = None) -> str:
    if name:
        return f"Будь ласка, {name}! Якщо ще щось виникне — пишіть."
    return "Будь ласка! Якщо ще щось виникне — пишіть."


def urgent_safety_reply(name: str | None = None) -> str:
    addr = f"{name}, " if name else ""
    return (
        f"{addr}це може бути небезпечно. НЕГАЙНО вимкніть машину з розетки і не вмикайте її. "
        "Не пробуйте виправити самостійно — зверніться в авторизований сервіс. "
        "Опишіть симптом їм по телефону перед візитом."
    )


def followup_yes_reply(name: str | None = None) -> str:
    if name:
        return f"Чудово, {name}! Радий що допомогло. Якщо ще щось виникне — пишіть."
    return "Чудово, радий що допомогло. Якщо ще щось виникне — пишіть."


def followup_no_reply(name: str | None = None) -> str:
    addr = f"{name}, " if name else ""
    return (
        f"{addr}жаль що не вийшло. Розкажіть, що саме сталося після того, як спробували: "
        "помилка та ж, інша, чи з'явились нові симптоми? Так зможу запропонувати інше рішення."
    )
