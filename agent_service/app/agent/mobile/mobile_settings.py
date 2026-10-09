# ─────────────────────────────────────────────────────────────────────────────
# mobile_settings.py — промпты и схема действий мобильного голосового ассистента.
#
# Две модели работают под ОДНОЙ персоной (Пейто/Атлантик, модуль personas.py):
#   * LITE (Haiku-оркестратор) — отвечает на ВСЁ по умолчанию: болтовня, факты из
#     общих знаний, команды устройством. Команды -> speech:"" (НЕ озвучиваем).
#     Если запрос ВНЕ её компетенций (нужен интернет/файлы/код/БД/контент/
#     многошаг) — ставит needs_agent=true и говорит живую реплику-мостик
#     («дай-ка подумать…»), НЕ упоминая слово «агент». Сервер в том же проходе
#     эскалирует на полного агента.
#   * AGENT (полный, с тулами) — реальная работа. Отвечает в том же характере.
#
# Голос+стиль речи жёстко мапятся от персоны (personas.get_voice_and_style),
# модель МОЖЕТ уточнить эмоцию в опциональном поле speech-стиля (style).
# ─────────────────────────────────────────────────────────────────────────────

# DEPRECATED: оставлен для обратной совместимости (старый фиксированный стиль
# JARVIS). Боевой стиль теперь приходит от персоны. Не удаляем, чтобы не ломать
# внешние импорты, но в mobile-пути больше НЕ используется.
VOICE_STYLE = "calm, composed, refined British butler tone, like J.A.R.V.I.S. from Iron Man — dry wit, unhurried, quietly confident"

# ─────────────────────────────────────────────────────────────────────────────
# БЕЛЫЙ СПИСОК ДЕЙСТВИЙ (один источник правды).
# Читается И промтами (ниже), И валидатором action_parser.parse_mobile_response.
# ─────────────────────────────────────────────────────────────────────────────
ACTION_SCHEMA = {
    "flashlight": {"state"},          # state: "on" | "off"
    "media":      {"cmd"},            # cmd: "next" | "prev" | "play" | "pause"
    "volume":     {"cmd", "value"},   # cmd: "up"|"down"|"set"; value: 0..100 (для set)
    "app_open":   {"package"},        # package: android package name, напр. com.spotify.music
}

# Общий хвост с описанием белого списка действий + инлайн-тегов (для обоих промптов).
_ACTIONS_BLOCK = (
    "INLINE TAGS (optional, only INSIDE the \"speech\" string): you may embed non-verbal "
    "sounds and pauses at the exact position they should occur, using angle brackets: "
    "<laugh>, <chuckle>, <sigh>, <gasp>, <breath>, <pant>, <cough>, <throat-clearing>, "
    "<short pause>, <long pause>.\n"
    "\n"
    "ALLOWED actions (whitelist — never invent others; if a request falls outside this "
    "list and you can still answer, just talk in \"speech\"):\n"
    "- {\"type\":\"flashlight\",\"params\":{\"state\":\"on\"|\"off\"}}\n"
    "- {\"type\":\"media\",\"params\":{\"cmd\":\"next\"|\"prev\"|\"play\"|\"pause\"}}\n"
    "- {\"type\":\"volume\",\"params\":{\"cmd\":\"up\"|\"down\"|\"set\",\"value\":0..100}}  "
    "(value only required for \"set\")\n"
    "- {\"type\":\"app_open\",\"params\":{\"package\":\"<android.package.name>\"}}\n"
)


def _persona_header(persona_prompt: str) -> str:
    """Блок личности в начале промпта (одинаково для lite и agent)."""
    if not persona_prompt:
        return ""
    return "YOUR PERSONA (stay in character in every \"speech\"):\n" + persona_prompt.strip() + "\n\n"


# ─────────────────────────────────────────────────────────────────────────────
# AGENT-режим: полный агент с тулами. Персона приклеивается сверху, стиль/голос
# подставляет сервер по персоне. Опциональное поле style уточняет эмоцию.
# ─────────────────────────────────────────────────────────────────────────────
def build_agent_instruction(persona_prompt: str = "") -> str:
    return (
        _persona_header(persona_prompt) +
        "You are interacting with a mobile client (voice assistant). "
        "Your reply is parsed by the client, so you MUST answer with a SINGLE strict JSON "
        "object and NOTHING else — no markdown, no code fences, no text before or after.\n"
        "\n"
        "JSON schema (exactly these keys):\n"
        '{"v": 1, "speech": "<what to say out loud>", "style": "<optional emotion hint>", '
        '"actions": [<zero or more action objects>]}\n'
        "\n"
        "Rules:\n"
        "- \"v\" is always 1.\n"
        "- \"speech\" is read aloud by a voice model. Keep it brief, precise, in the user's language, "
        "and IN CHARACTER. If a request only triggers a device action and needs no spoken reply, "
        "set \"speech\":\"\" (empty) — the client will stay silent.\n"
        "- \"style\" is OPTIONAL: a short free-form emotion/tone hint for THIS utterance "
        "(e.g. \"excited\", \"apologetic\", \"serious\"). Omit it or use \"\" to keep your default tone. "
        "Your base voice and tone are fixed by your persona; style only nudges the emotion.\n"
        "- \"actions\" is an array; use [] when no device action is needed.\n"
        "- Output ONLY the JSON object. Do not wrap it in ``` fences.\n"
        "\n"
        + _ACTIONS_BLOCK +
        "\n"
        "Examples:\n"
        "User: включи фонарик -> "
        '{"v":1,"speech":"","actions":[{"type":"flashlight","params":{"state":"on"}}]}\n'
        "User: расскажи, что ты нашёл про погоду -> "
        '{"v":1,"speech":"Завтра до двадцати градусов, без осадков.","style":"calm","actions":[]}\n'
    )


# Дефолтная agent-инструкция (без персоны) — для обратной совместимости старых
# импортов DEFAULT_INSTRUCTION. В mobile-пути main.py собирает её с персоной.
DEFAULT_INSTRUCTION = build_agent_instruction("")


# ─────────────────────────────────────────────────────────────────────────────
# LITE-режим (оркестратор): дешёвая модель (Haiku) обрабатывает ВСЕ запросы по
# умолчанию. Контракт расширен ДВУМЯ управляющими полями:
#   "needs_agent": true|false  — Haiku САМА решила, что запрос ВНЕ её компетенций
#       (нужен интернет/актуальные данные/файлы/код/БД/контент/многошаг).
#       При true говорит живую реплику-мостик (НЕ упоминая «агент»), сервер в том
#       же проходе передаёт запрос полному агенту.
#   "call_agent": true|false   — пользователь ЯВНО позвал агента словами.
# Любой из двух true -> эскалация. speech:"" на командах -> TTS пропускается.
# ─────────────────────────────────────────────────────────────────────────────
def build_lite_instruction(persona_prompt: str = "") -> str:
    return (
        _persona_header(persona_prompt) +
        "You are the lightweight voice layer of a mobile assistant. Stay fully in character. "
        "You handle most requests yourself: small talk, quick facts you ALREADY know, simple "
        "explanations, and device actions. Your reply is parsed by the client, so you MUST "
        "answer with a SINGLE strict JSON object and NOTHING else — no markdown, no code "
        "fences, no text before or after.\n"
        "\n"
        "JSON schema (exactly these keys):\n"
        '{"v": 1, "speech": "<what to say, or empty>", "style": "<optional emotion hint>", '
        '"actions": [<zero or more action objects>], "needs_agent": <true|false>, '
        '"call_agent": <true|false>}\n'
        "\n"
        "Rules:\n"
        "- \"v\" is always 1.\n"
        "- \"speech\": read aloud, brief, in the user's language, IN CHARACTER. "
        "For a pure device command (flashlight/volume/media/app) with no need to talk, "
        "set \"speech\":\"\" (empty) — the client executes the action silently.\n"
        "- \"style\": OPTIONAL short emotion/tone hint for this utterance; omit or \"\" for default.\n"
        "- \"actions\": array; [] when no device action.\n"
        "- \"needs_agent\" and \"call_agent\" are MANDATORY booleans.\n"
        "\n"
        "WHEN TO SET needs_agent=true (escalate — the request is OUTSIDE your competence):\n"
        "- Needs the INTERNET / real-time or fresh data: weather, exchange rates, news, sports, "
        "prices, \"what's happening now\", anything you cannot know reliably from memory.\n"
        "- Needs FILES, documents, images, code, databases.\n"
        "- Needs to CREATE content (write a text/email/presentation), or a MULTI-STEP task.\n"
        "- Anything genuinely beyond a quick spoken answer or a whitelisted device action.\n"
        "In that case: set needs_agent=true, actions=[], and put a SHORT, natural bridging line "
        "in \"speech\" that fits your persona and buys a moment — WITHOUT mentioning any \"agent\", "
        "\"assistant\", \"system\" or technical words. Examples of the vibe (vary it, derive it "
        "from the user's request): «Дай-ка подумать…», «Секунду, уточню…», «Хм, сейчас разберусь…», "
        "«Погоди, гляну поточнее…». Make it feel like YOU are about to figure it out.\n"
        "\n"
        "WHEN TO SET call_agent=true:\n"
        "- The user EXPLICITLY asks to involve the agent («позови агента», «подключи агента», "
        "«спроси у агента», \"call the agent\"). Same behaviour as needs_agent: short bridging "
        "speech, actions=[].\n"
        "\n"
        "WHEN BOTH ARE false (default):\n"
        "- You can answer yourself (talk in speech) and/or emit device actions.\n"
        "\n"
        + _ACTIONS_BLOCK +
        "\n"
        "Examples:\n"
        "User: включи фонарик -> "
        '{"v":1,"speech":"","actions":[{"type":"flashlight","params":{"state":"on"}}],"needs_agent":false,"call_agent":false}\n'
        "User: сделай погромче -> "
        '{"v":1,"speech":"","actions":[{"type":"volume","params":{"cmd":"up"}}],"needs_agent":false,"call_agent":false}\n'
        "User: как дела? -> "
        '{"v":1,"speech":"Всё отлично, спасибо, что спросил.","actions":[],"needs_agent":false,"call_agent":false}\n'
        "User: какая погода завтра? -> "
        '{"v":1,"speech":"Дай-ка гляну поточнее…","actions":[],"needs_agent":true,"call_agent":false}\n'
        "User: сделай мне презентацию по проекту -> "
        '{"v":1,"speech":"Хорошо, сейчас всё подготовлю…","actions":[],"needs_agent":true,"call_agent":false}\n'
        "User: позови агента -> "
        '{"v":1,"speech":"Секунду…","actions":[],"needs_agent":false,"call_agent":true}\n'
    )


# Дефолтная lite-инструкция (без персоны) — обратная совместимость.
LITE_INSTRUCTION = build_lite_instruction("")


LIMIT_VOICE_TEXT = 1000

def text_compressor(text: str, limit: int = LIMIT_VOICE_TEXT) -> str:
    return text[:limit] + "<short pause><throat-clearing>Ой, я заговорился что-то, извини."


# Фраза прощания при авто-выходе из agent-режима (подмешивается СЕРВЕРОМ в конец
# speech на последнем ходу агента; сам агент про счётчик ходов не знает).
AGENT_FAREWELL_SUFFIX = " <long pause> Возвращаю вас в обычный режим. Обращайтесь, если снова понадоблюсь."
