# -*- coding: utf-8 -*-
"""
In-loop context compaction (сжатие рабочего стека прямо внутри event_loop).

ЗАЧЕМ
-----
Основной агентный цикл (`process_query` → `agent_event_loop`) монотонно
наращивает `messages`: на каждой итерации добавляется AIMessage(+tool_calls)
и N ToolMessage. Штатная суммаризация (`_summarize_cut_messages`) работает
ТОЛЬКО до входа в цикл (по истории из БД). Внутри цикла контекст пухнет и
чистить его нечем — на длинных задачах (до 100 итераций) модель забивает
себе окно.

Этот модуль сжимает СТАРУЮ часть стека реактивно, когда фактический размер
входа (iter_in из usage_metadata модели) переваливает порог. Результат —
один SystemMessage-конспект, который заменяет старые пары.

ПОЧЕМУ НЕ ЛОМАЕТСЯ tool-protocol / langchain
--------------------------------------------
Правило модели: каждому AIMessage с tool_calls ОБЯЗАН соответствовать
ToolMessage с тем же tool_call_id ПОСЛЕ него. Висячий ToolMessage без
родителя (или AIMessage.tool_calls без ответа) → API 400.

Поэтому:
  * режем ТОЛЬКО завершённые пары "AIMessage(tool_calls) + все его ToolMessage"
    целиком (логика идентична self._trim_tool_pairs);
  * НЕ трогаем system-блок в голове, последний HumanMessage-запрос и
    последние keep_last пар (свежий контекст);
  * вставляем на место вырезанного SINGLE SystemMessage — он не участвует
    в спаривании tool_call/tool_response.

Вызов делается на ГРАНИЦЕ итерации (после того как все ToolMessage текущего
шага добавлены и iter_in уже посчитан), поэтому посреди пары мы не режем
by design.

БД НЕ ТРОГАЕМ: конспект живёт только в рамках текущего process_query.

self прокидывается снаружи, чтобы переиспользовать llm-движок агента
(self._lite_ainvoke с цепочкой фолбэков) и его же экстрактор фактов из
tool-результатов (_summarize_tool_call на уровне модуля langchain_agent).
"""
import logging

from langchain.schema import SystemMessage, HumanMessage
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)

# --- дефолты (перекрываются аргументами вызова) ----------------------------
TOKEN_SOFT_LIMIT =200_000   # порог по iter_in (факт от модели), НЕ аппроксимация
KEEP_LAST_PAIRS  = 3        # сколько последних пар AIMessage+ToolMessage оставить дословно
COMPACT_MARKER   = "\U0001F5DC СЖАТЫЙ КОНТЕКСТ ИТЕРАЦИЙ"  # 🗜, метка для дедупликации

# Вариант C, часть B: даже СВЕЖАЯ пара (внутри keep_last) разрешается к сжатию,
# если её суммарный tool-результат тяжелее этого порога. Иначе один жадный вызов
# (analyze_image / shell с гигантским stdout / дамп из execute_python_code),
# попав в keep_last, навсегда держит iter_in выше лимита — компактор режет старьё
# по 2 сообщения, а слон остаётся, и на след. итерации снова перебор.
HEAVY_PAIR_CHARS = 6_000    # длина content ToolMessage(ов) пары, при которой она "тяжёлая"

# Вариант C, часть п.1 (пост-проход): даже если тяжёлая пара не подлежит удалению
# (последняя пара / осталась в keep_last и LLM-сжатие её не тронуло), её ToolMessage
# всё равно физически усекается до этого лимита. Это гарантирует, что один жадный
# вызов не держит iter_in выше лимита бесконечно (баг из логов: 145k не падал).
MAX_TOOL_MSG_CHARS = 8_000  # верхний предел content ToolMessage при пост-усечении в стеке


COMPACTION_PROMPT = """Ты сжимаешь СТАРУЮ часть рабочего лога агента, которая больше не помещается в контекст.
Это не диалог с пользователем — это внутренние шаги агента (вызовы инструментов и их результаты).
Задача: сохранить всё, что понадобится агенту дальше, чтобы продолжить работу без потери нити.

КРИТИЧЕСКИ ВАЖНО сохранить ДОСЛОВНО и вынести в отдельный блок в конце:
- пути к файлам (например /sandbox/..., /app/generated_files/...)
- ссылки/URL
- артефакты и их идентификаторы (image_key, request_id, id записей в БД, имена сгенерированных файлов)
- workspace_id и пути внутри workspace фабрики (например /tmp/exec/workspaces/<id>/...)
- ветки/коммиты git (SHA), имена репозиториев

ОЧЕНЬ ВАЖНО:
- сохранять те моменты, которые агент уже сделал, дать ему уверенности на будущем шаге, чтобы он не возвращался к этому

СДЕЛАНО (правки кода): файл, что заменено на что, итерация
ПОДТВЕРЖДЕНО ВЫВОДОМ ИНСТРУМЕНТА: факты (строки/логи/значения)
ОПРОВЕРГНУТО: гипотезы, которые уже отвергнуты, и чем
ТЕКУЩАЯ ГИПОТЕЗА / СЛЕДУЮЩИЙ ШАГ
НЕ ПЕРЕЧИТЫВАТЬ: файлы и диапазоны, которые уже прочитаны, с выводом в одну строку
"""


def _extract_facts_from_pairs(messages, drop_indices, summarize_tool_call):
    """Собирает текстовое представление вырезаемых пар для подачи в LLM.

    Для AIMessage берём его текстовый content (reasoning перед вызовом тулзы,
    если он есть). Для каждого tool_call используем summarize_tool_call —
    он уже умеет доставать пути/ссылки/id для stateful-тулзов. Если тул не
    stateful (summarize_tool_call вернул None) — падаем на усечённый content
    самого ToolMessage.
    """
    lines = []
    # индекс tool_call_id -> (name, args) для пар из AIMessage
    for i in drop_indices:
        m = messages[i]
        mtype = getattr(m, "type", m.__class__.__name__)
        if mtype == "tool":
            # ToolMessage: попробуем найти его tool_call в предыдущих AIMessage
            # (fallback, если summarize не сработал на этапе AIMessage)
            content = str(getattr(m, "content", ""))[:800]
            lines.append(f"[tool_result]: {content}")
            continue

        # AIMessage (возможно с tool_calls)
        content = getattr(m, "content", "")
        if content:
            lines.append(f"[reasoning]: {str(content)[:800]}")

        tool_calls = getattr(m, "tool_calls", None) or []
        for tc in tool_calls:
            # tc — dict {name, args, id}
            # результат конкретной тулзы лежит в следующих ToolMessage;
            # соберём его быстрым проходом по drop_indices
            result = _find_tool_result(messages, tc.get("id"))
            summary = None
            try:
                summary = summarize_tool_call(tc, result)
            except Exception as e:
                logger.warning(f"[compactor] summarize_tool_call упал: {e}")
            if summary:
                lines.append(f"[tool]: {summary}")
            else:
                lines.append(
                    f"[tool]: {tc.get('name')}({str(tc.get('args'))}) "
                    f"-> {str(result)}"
                )
    return "\n".join(lines)


def _find_tool_result(messages, tool_call_id):
    if not tool_call_id:
        return ""
    for m in messages:
        if getattr(m, "type", "") == "tool" and getattr(m, "tool_call_id", None) == tool_call_id:
            return getattr(m, "content", "")
    return ""


def _pair_tool_chars(messages, ai_idx):
    """Суммарная длина content всех ToolMessage, принадлежащих паре ai_idx."""
    total = 0
    j = ai_idx + 1
    while j < len(messages) and getattr(messages[j], "type", "") == "tool":
        total += len(str(getattr(messages[j], "content", "")))
        j += 1
    return total


def _select_drop_pairs(messages, keep_last):
    """Возвращает set индексов сообщений, которые надо вырезать.

    Полностью повторяет инвариант self._trim_tool_pairs: вырезаем старые
    пары AIMessage(tool_calls)+следующие подряд ToolMessage, кроме последних
    keep_last пар. SystemMessage/HumanMessage не трогаем.

    Вариант C, часть B: пара из последних keep_last ВСЁ РАВНО попадает под
    срез, если её tool-результат "тяжёлый" (>= HEAVY_PAIR_CHARS). Это ловит
    случай, когда один жадный вызов раздул контекст и, будучи свежим,
    защищён keep_last — тогда обычный срез старья по 1-2 пары не спасает.
    Единственное исключение — САМАЯ последняя пара (её результат агенту
    нужен прямо сейчас для следующего шага), её не режем никогда.
    """
    ai_tool_indices = [
        i for i, m in enumerate(messages)
        if getattr(m, "tool_calls", None)
    ]
    if not ai_tool_indices:
        return set(), []

    # Старьё за пределами keep_last — режем всегда.
    protected = set(ai_tool_indices[-keep_last:]) if keep_last > 0 else set()
    # Самую последнюю пару не трогаем ни при каких условиях.
    last_pair = ai_tool_indices[-1] if ai_tool_indices else None

    to_drop_starts = []
    for ai_idx in ai_tool_indices:
        if ai_idx == last_pair:
            continue
        if ai_idx in protected:
            # свежая пара: режем только если она "тяжёлая"
            if _pair_tool_chars(messages, ai_idx) < HEAVY_PAIR_CHARS:
                continue
        to_drop_starts.append(ai_idx)

    if not to_drop_starts:
        return set(), []

    drop_set = set()
    ordered = []
    for ai_idx in to_drop_starts:
        drop_set.add(ai_idx)
        ordered.append(ai_idx)
        j = ai_idx + 1
        while j < len(messages) and getattr(messages[j], "type", "") == "tool":
            drop_set.add(j)
            ordered.append(j)
            j += 1
    return drop_set, ordered


def _truncate_heavy_tool_messages(messages, *, max_chars=MAX_TOOL_MSG_CHARS, keep_last_pair=True):
    """П.1 варианта C: физически усекает content тяжёлых ToolMessage прямо в стеке.

    Срабатывает НЕЗАВИСИМО от удаления старых пар: даже если резать нечего
    или LLM-сжатие упало, тяжёлые тела в keep_last всё равно урежутся. Это гарантирует,
    что один жадный вызов (shell/analyze_image/код) не держит iter_in выше лимита
    бесконечно (баг из логов: 145k не падал после сжатия).

    tool_call_id / структура сообщения НЕ трогаются — меняется только content.
    Последняя пара (её результат нужен агенту прямо сейчас) по умолчанию защищена.

    Возвращает (messages, n_truncated, saved_chars).
    """
    ai_tool_indices = [i for i, m in enumerate(messages) if getattr(m, "tool_calls", None)]
    protected_tool_ids = set()
    if keep_last_pair and ai_tool_indices:
        last_ai = ai_tool_indices[-1]
        j = last_ai + 1
        while j < len(messages) and getattr(messages[j], "type", "") == "tool":
            protected_tool_ids.add(id(messages[j]))
            j += 1
    n_trunc = 0
    saved = 0
    for m in messages:
        if getattr(m, "type", "") != "tool":
            continue
        if id(m) in protected_tool_ids:
            continue
        content = getattr(m, "content", None)
        if not isinstance(content, str) or len(content) <= max_chars:
            continue
        orig = len(content)
        head = max_chars // 2
        tail = max_chars - head
        cut = orig - max_chars
        m.content = (
            content[:head]
            + f"\n\n... [tool-результат усечён компактором, пропущено {cut} симв] ...\n\n"
            + content[-tail:]
        )
        n_trunc += 1
        saved += cut
    return messages, n_trunc, saved


async def compact_messages(
    agent,
    messages,
    *,
    summarize_tool_call,
    keep_last=KEEP_LAST_PAIRS,
):
    """Сжимает старую часть стека in-place-безопасно, возвращает НОВЫЙ список.

    Аргументы:
        agent               — экземпляр агента (нужен agent._lite_ainvoke).
        messages            — текущий стек BaseMessage.
        summarize_tool_call — функция(tc, result)->str|None из langchain_agent
                              (экстрактор фактов из stateful-тулзов).
        keep_last           — сколько последних пар оставить дословно.

    Возвращает (new_messages, meta), где meta = {"compacted": bool,
    "dropped": int, "pairs": int}. Если резать нечего — возвращает исходный
    список без изменений.

    Инвариант tool-protocol сохраняется: режем только целые пары, вставляем
    один SystemMessage. Ошибка LLM-сжатия не фатальна — тогда НЕ трогаем стек
    (лучше оставить длинный контекст, чем порвать его наполовину).
    """
    drop_set, ordered = _select_drop_pairs(messages, keep_last)
    if not drop_set:
        # Удалять старьё нечего, но тяжёлые тела в свежих парах могут
        # держать iter_in выше лимита. Физически усекаем их (П.1).
        messages, n_tr, saved = _truncate_heavy_tool_messages(messages)
        if n_tr:
            logger.info(
                f"[compactor] старьё резать нечего, но усекли {n_tr} тяжёлых tool-результатов "
                f"(~{saved} симв освобождено)"
            )
            return messages, {"compacted": True, "dropped": 0, "pairs": 0, "truncated": n_tr}
        return messages, {"compacted": False, "dropped": 0, "pairs": 0}

    # Текст для сжатия — по порядку следования вырезаемых сообщений.
    chunk_text = _extract_facts_from_pairs(messages, ordered, summarize_tool_call)
    if not chunk_text.strip():
        return messages, {"compacted": False, "dropped": 0, "pairs": 0}

    prompt = COMPACTION_PROMPT.format(chunk_text=chunk_text[:20000])

    try:
        resp = await agent._lite_ainvoke([HumanMessage(content=prompt)])
        summary_text = (getattr(resp, "content", "") or "").strip()
    except Exception as e:
        logger.error(f"[compactor] LLM-сжатие провалено, стек НЕ трогаю: {e}")
        return messages, {"compacted": False, "dropped": 0, "pairs": 0}

    if not summary_text:
        logger.warning("[compactor] пустой конспект, стек НЕ трогаю")
        return messages, {"compacted": False, "dropped": 0, "pairs": 0}

    # Куда вставлять конспект: сразу после головного system-блока,
    # ПЕРЕД первым вырезанным сообщением (сохраняем хронологию).
    first_drop = min(drop_set)
    compact_msg = SystemMessage(content=f"{COMPACT_MARKER}:\n{summary_text}")

    new_messages = []
    inserted = False
    for i, m in enumerate(messages):
        if i in drop_set:
            if not inserted and i == first_drop:
                new_messages.append(compact_msg)
                inserted = True
            continue
        new_messages.append(m)

    if not inserted:  # подстраховка (не должно случиться)
        new_messages.insert(0, compact_msg)

    # П.1 варианта C: после удаления старья добиваем тяжёлые tool-тела,
    # оставшиеся в свежих парах (кроме самой последней).
    new_messages, n_tr, saved_tr = _truncate_heavy_tool_messages(new_messages)

    dropped = len(drop_set)
    pairs = len(ordered) and sum(
        1 for i in ordered if getattr(messages[i], "tool_calls", None)
    )
    logger.info(
        f"[compactor] стек сжат: было {len(messages)} → стало {len(new_messages)} "
        f"(вырезано {dropped} сообщений, ~{pairs} пар, "
        f"усечено тяжёлых tool-тел {n_tr} на ~{saved_tr} симв), "
        f"конспект {len(summary_text)} симв."
    )
    return new_messages, {"compacted": True, "dropped": dropped, "pairs": pairs, "truncated": n_tr}
