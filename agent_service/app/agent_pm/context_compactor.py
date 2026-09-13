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
TOKEN_SOFT_LIMIT = 80_000   # порог по iter_in (факт от модели), НЕ аппроксимация
KEEP_LAST_PAIRS  = 3        # сколько последних пар AIMessage+ToolMessage оставить дословно
COMPACT_MARKER   = "\U0001F5DC СЖАТЫЙ КОНТЕКСТ ИТЕРАЦИЙ"  # 🗜, метка для дедупликации


COMPACTION_PROMPT = """Ты сжимаешь СТАРУЮ часть рабочего лога агента, которая больше не помещается в контекст.
Это не диалог с пользователем — это внутренние шаги агента (вызовы инструментов и их результаты).
Задача: сохранить всё, что понадобится агенту дальше, чтобы продолжить работу без потери нити.

КРИТИЧЕСКИ ВАЖНО сохранить ДОСЛОВНО и вынести в отдельный блок в конце:
- пути к файлам (например /sandbox/..., /app/generated_files/...)
- ссылки/URL
- артефакты и их идентификаторы (image_key, request_id, id записей в БД, имена сгенерированных файлов)
- workspace_id и пути внутри workspace фабрики (например /tmp/exec/workspaces/<id>/...)
- ветки/коммиты git (SHA), имена репозиториев

В основной части (сжатая проза, не более 300 слов) перечисли ФАКТАМИ:
- какая задача решалась и на каком она этапе
- какие инструменты вызывались и с каким ключевым результатом
- незавершённые договорённости и что агент планировал сделать дальше
- конкретные числа/имена/параметры, которые ещё пригодятся

НЕ включай: приветствия, размышления модели вслух, воду, дублирование.

Фрагмент лога для сжатия:
{chunk_text}

Формат ответа СТРОГО такой:

СВОДКА:
<сжатая проза фактами>

АРТЕФАКТЫ И ПУТИ:
- <путь/ссылка/id/workspace/коммит — по одному на строку; если ничего нет, напиши "нет">
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


def _select_drop_pairs(messages, keep_last):
    """Возвращает set индексов сообщений, которые надо вырезать.

    Полностью повторяет инвариант self._trim_tool_pairs: вырезаем старые
    пары AIMessage(tool_calls)+следующие подряд ToolMessage, кроме последних
    keep_last пар. SystemMessage/HumanMessage не трогаем.
    """
    ai_tool_indices = [
        i for i, m in enumerate(messages)
        if getattr(m, "tool_calls", None)
    ]
    if len(ai_tool_indices) <= keep_last:
        return set(), []

    to_drop_starts = ai_tool_indices[:-keep_last]
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

    dropped = len(drop_set)
    pairs = len(ordered) and sum(
        1 for i in ordered if getattr(messages[i], "tool_calls", None)
    )
    logger.info(
        f"[compactor] стек сжат: было {len(messages)} → стало {len(new_messages)} "
        f"(вырезано {dropped} сообщений, ~{pairs} пар), конспект {len(summary_text)} симв."
    )
    return new_messages, {"compacted": True, "dropped": dropped, "pairs": pairs}
