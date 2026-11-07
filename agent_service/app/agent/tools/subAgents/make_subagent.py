"""
make_subagent.py
────────────────
Фабрика тулы call_subagent для главного агента (PMыча).

КОНЦЕПЦИЯ
─────────
PMыч вызывает call_subagent, передавая:
  - agent_name    — роль субагента (ключ в DEFAULT_SYSTEM_PROMPTS или произвольное)
  - task          — задача в свободной форме
  - allowed_tools — список имён тулзов, доступных субагенту
  - system_prompt — опциональный системный промт (иначе берётся дефолтный)
  - max_iter      — максимум итераций (1–12, дефолт 6)
  - extra_context — доп. контекст: результаты других субагентов, промежуточные данные

Фабрика получает agent_instance (self у KnowledgeAgent), захватывает его в замыкание.
Тула динамически добавляется при каждом запросе пользователя.

КАК ВСТРОИТЬ
────────────
В конце _build_tools (или отдельным вызовом):

    from .tools.subAgents.make_subagent import make_subagent_tool
    tools.append(make_subagent_tool(self))

СТАТУСЫ СУБАГЕНТОВ (send_status)
─────────────────────────────────
Субагент получает request_id и agent_name через замыкание.
Каждый шаг субагента отправляет статус через agent._send_status() с префиксом:

    "[data_analyst] 💭 Думаю…"
    "[researcher]  🔧 вызывает: search_internet"
    "[researcher]  ✅ завершён (3 итерации)"

Протокол WS-сообщения остаётся прежним:
    { "type": "agent_status", "status": "..." }

Фронт (app.js) должен различать обычный статус PMыча и статус субагента по префиксу
в квадратных скобках — [agent_name]. Это позволяет рендерить отдельный блок
для каждого субагента и складывать их под друг другом.

ПАРАЛЛЕЛЬНЫЙ ЗАПУСК
───────────────────
PMыч может вызвать call_subagent несколько раз в одном tool_call батче.
asyncio.gather запустит их параллельно — статусы придут вперемешку, но
фронт разделяет их по [agent_name]-префиксу.

УПРОЩЁННЫЙ EVENT LOOP (vs process_query)
─────────────────────────────────────────
Намеренно убрано:
  ✗ trimmed_history / rolling_summary / session_summary
  ✗ journal_text (бортовой журнал)
  ✗ database.add_message (запись в БД)
  ✗ request_more_steps / extension_tool
  ✗ total_input_tokens / total_output_tokens
  ✗ files_to_send / seen_paths
  ✗ _summarize_tool_call / tool_trace

Оставлено:
  ✓ _build_tools + фильтрация по allowed_tools
  ✓ Свой system_prompt (роль субагента)
  ✓ Ограниченный max_iter с жёстким потолком MAX_ITER_CAP
  ✓ asyncio.gather для параллельных tool_calls внутри одной итерации
  ✓ Гашение промежуточного content при наличии tool_calls (как в process_query)
  ✓ Нормализация результата тулзы в строку (str / dict / Exception)
  ✓ Предупреждение о missing tools (тихая фильтрация)
  ✓ Форсирование финального ответа на последней итерации (снятие bind_tools)
  ✓ _send_status с [agent_name]-префиксом на каждом шаге
  ✓ Логирование каждого шага
"""

import asyncio
import json
import logging
from typing import Any

from langchain.tools import tool
from langchain.schema import HumanMessage, SystemMessage, AIMessage
from langchain_core.messages import ToolMessage
from agent.common.tool_output import clip_text, fit_result

from agent.tool_status_messages import get_tool_status, get_tools_status

# Раньше результат тула уходил субагенту целиком; песочница больше не режет
# stdout, поэтому ставим верхний предел (с запасом, чтобы не урезать поиск).
SUBAGENT_MAX_TOOL_MSG_CHARS = 20_000

logger = logging.getLogger(__name__)


# ── Дефолтные системные промты ────────────────────────────────────────────────

DEFAULT_SYSTEM_PROMPTS: dict[str, str] = {
    "data_analyst": (
        "Ты — аналитик данных. Выполняй SQL-запросы, интерпретируй результаты, строй графики. "
        "Перед SQL всегда вызывай get_db_schema. "
        "Возвращай: факты + числа + краткий вывод. Без лирики."
    ),
    "scraper": (
        "Ты — веб-скрапер. Ищи готовые скрипты через search_code_library перед написанием нового. "
        "Playwright: только domcontentloaded, без networkidle. "
        "Файлы — в output/. После успеха → save_to_code_library."
    ),
    "researcher": (
        "Ты — исследователь. Ищи информацию через search_internet, читай страницы через parse_web_page. "
        "Возвращай: URL источника + ключевые факты + дата материала."
    ),
    "content_maker": (
        "Ты — генератор контента. Создавай PPTX, DOCX, PNG по описанию. "
        "Шрифты Inter: /app/fonts/extras/ttf/. Файлы в output/. "
        "OPENBLAS_NUM_THREADS=1 в начале каждого скрипта. "
        "ОБЯЗАТЕЛЬНО: после execute_python_code верни полный путь из saved_files[0]['path']."
    ),
    "kb_manager": (
        "Ты — менеджер базы знаний. Перед INSERT проверяй дубликаты через SELECT. "
        "DELETE запрещён — используй knowledge_review. "
        "Одна статья = одна запись. Без объединения источников."
    ),
    "code_writer": (
        "Ты — Python-разработчик. Пишешь чистый рабочий код. "
        "Стек: pandas, numpy, matplotlib, Pillow, python-pptx, python-docx, reportlab. "
        "Всегда: import os; os.environ['OPENBLAS_NUM_THREADS']='1'. "
        "Файлы в output/. Без лишних комментариев."
    ),
}

# Жёсткий потолок итераций субагента — защита от бесконечных петель
MAX_ITER_CAP = 12

# Эмодзи-статусы для разных фаз субагента
_STATUS_THINKING = "💭 думает…"
_STATUS_DONE     = "✅ завершён"
_STATUS_ERROR    = "❌ ошибка"


def _fmt(agent_name: str, text: str) -> str:
    """Форматирует статус с [agent_name]-префиксом для фронта."""
    return f"[{agent_name}] {text}"


# ── Фабрика ───────────────────────────────────────────────────────────────────

_SUBAGENT_BLOCKED_TOOLS = {"call_subagent", "request_more_steps", "no_tools_selected"}


def make_subagent_tool(agent_instance, parent_tools=None):
    """
    Принимает экземпляр KnowledgeAgent (self) и список доступных родительскому
    агенту инструментов (parent_tools).
    Возвращает @tool call_subagent, готовую к добавлению в список тулзов PMыча.

    Пример добавления в _build_tools:

        from .tools.subAgents.make_subagent import make_subagent_tool
        tools.append(make_subagent_tool(self, parent_tools=list(tools)))
    """

    agent = agent_instance  # захват self в замыкание
    available_parent_tools = [
        t for t in (parent_tools or [])
        if getattr(t, "name", None) and t.name not in _SUBAGENT_BLOCKED_TOOLS
    ]

    @tool
    async def call_subagent(
        agent_name: str,
        task: str,
        allowed_tools: list[str],
        system_prompt: str = "",
        max_iter: int = 6,
        extra_context: str = "",
    ) -> str:
        """
        Delegate the task to a specialized subagent.

        The subagent is an isolated LLM cycle with a limited set of tools.
        It has no access to chat history, memory, or the flight log.
        The result (a string) is returned to PMych — he responds to the user.

        Arguments:
            agent_name    — the role of the subagent. Standard:
                            "data_analyst", "scraper", "researcher", "content_maker", "kb_manager", "code_writer".
                            An arbitrary name is acceptable — in this case, system_prompt is required.
            task          — a detailed description of the task. The more detailed, the better.
            allowed_tools — a list of tool names that the subagent can call.
                            Invalid names are silently filtered out.
                            Example: ["execute_python_code", "search_code_library"]
            system_prompt — the subagent’s system prompt. If empty, the default prompt is taken
                            based on agent_name from DEFAULT_SYSTEM_PROMPTS.
            max_iter      — the maximum number of iterations in the agent cycle (1–12, default is 6).
            extra_context — additional context: results from other subagents,
                            intermediate data, clarifications.

        Returns:
            The final response of the subagent as a string.
            In case of an error — a string with a description of the error.

        Parallel execution:
            Call `call_subagent` multiple times in a single batch — LangChain will run them
            via `asyncio.gather` in parallel; the results will arrive independently.
            The statuses of each subagent are marked with a [agent_name]-prefix at the front.
        """
        # ── Получаем request_id из агента (нужен для _send_status) ──────────
        # agent.current_request_id должен быть выставлен в process_query
        # перед стартом агентного цикла.
        request_id: str = agent._ctx_get("request_id", getattr(agent, "current_request_id", "")) or ""

        async def send(text: str) -> None:
            """Fire-and-forget статус с [agent_name]-префиксом."""
            if not request_id:
                return
            try:
                await agent._send_status(_fmt(agent_name, text), request_id)
            except Exception:
                pass  # fire-and-forget, не блокируем агентный цикл

        logger.info(
            "[SubAgent] Запуск '%s' | tools=%s | max_iter=%d | task=%.80s",
            agent_name, allowed_tools, max_iter, task,
        )

        await send(_STATUS_THINKING)

        # ── 1. Системный промт ────────────────────────────────────────────────
        sys_prompt = (
            system_prompt.strip()
            or DEFAULT_SYSTEM_PROMPTS.get(agent_name, "")
            or f"Ты — субагент с ролью '{agent_name}'. Выполни задачу точно и лаконично."
        )
        if extra_context:
            sys_prompt += f"\n\n## Дополнительный контекст\n{extra_context}"

        # ── 2. Строим и фильтруем тулзы (строго из parent_tools без call_subagent) ─
        all_tools = [
            t for t in available_parent_tools
            if getattr(t, "name", None) and t.name not in _SUBAGENT_BLOCKED_TOOLS
        ]

        allowed_set  = set(allowed_tools) - _SUBAGENT_BLOCKED_TOOLS
        filtered     = [t for t in all_tools if t.name in allowed_set]
        filtered_map = {t.name: t for t in filtered}

        missing = set(allowed_tools) - set(filtered_map)
        if missing:
            logger.warning(
                "[SubAgent] '%s': тулзы недоступны или запрещены (проигнорированы): %s",
                agent_name, missing,
            )

        if not filtered:
            await send(_STATUS_ERROR)
            available_names = sorted(t.name for t in all_tools)
            return (
                f"Для субагента '{agent_name}' не переданы доступные инструменты: "
                f"allowed_tools={sorted(set(allowed_tools))}. "
                f"Доступные в текущем запросе инструменты: {available_names}."
            )

        # ── 3. Привязываем тулзы к LLM ───────────────────────────────────────
        from ...core.agent_provider import get_vision_fallback_order, resolve_model_tag
        cur_tag = resolve_model_tag(
            agent._ctx_get("agent_model", getattr(agent, "current_agent_model", None)),
            agent_models=agent.AGENT_MODELS,
        )
        base_llm = getattr(agent, "llm", None) or agent.AGENT_MODELS.get(cur_tag)
        if base_llm is None:
            for _fb_tag in get_vision_fallback_order():
                base_llm = agent.AGENT_MODELS.get(_fb_tag)
                if base_llm is not None:
                    break
        if base_llm is None and agent.AGENT_MODELS:
            base_llm = next(iter(agent.AGENT_MODELS.values()))
        llm_with_tools = base_llm.bind_tools(filtered)


        # ── 4. Начальный стек сообщений ───────────────────────────────────────
        messages = [
            SystemMessage(content=sys_prompt),
            HumanMessage(content=task),
        ]

        # ── 5. Агентный цикл ──────────────────────────────────────────────────
        capped_iter  = min(max(1, max_iter), MAX_ITER_CAP)
        final_answer = ""
        iteration    = 0

        for iteration in range(capped_iter):
            logger.info(
                "[SubAgent] '%s' итерация %d/%d",
                agent_name, iteration + 1, capped_iter,
            )

            try:
                # Всегда используем llm_with_tools, чтобы не ломать схему API
                # наличием ToolMessage в истории без объявленных инструментов.
                response = await llm_with_tools.ainvoke(messages)
            except Exception as e:
                logger.error(
                    "[SubAgent] '%s' ошибка LLM на итерации %d: %s",
                    agent_name, iteration + 1, e,
                )
                await send(_STATUS_ERROR)
                final_answer = f"Ошибка LLM субагента '{agent_name}': {e}"
                break

            # Гасим промежуточный content при наличии tool_calls
            if getattr(response, "tool_calls", None) and getattr(response, "content", None):
                response.content = ""

            messages.append(response)

            # Нет tool_calls — субагент дал финальный ответ (штатный выход)
            if not getattr(response, "tool_calls", None):
                final_answer = response.content or ""
                logger.info(
                    "[SubAgent] '%s' финал на итерации %d",
                    agent_name, iteration + 1,
                )
                break

            # ── Статус: какие тулзы вызываем ──────────────────────────────────
            tool_names = [tc["name"] for tc in response.tool_calls]
            await send(f"{get_tools_status(tool_names)}")

            # ── Параллельный вызов тулзов (asyncio.gather) ───────────────────
            async def _invoke_one(tc: dict) -> tuple[dict, Any]:
                t_name = tc["name"]
                t_args = tc.get("args", {})

                if t_name not in filtered_map:
                    msg = f"Инструмент '{t_name}' не входит в allowed_tools субагента '{agent_name}'"
                    logger.warning("[SubAgent] '%s': %s", agent_name, msg)
                    return tc, msg

                logger.info(
                    "[SubAgent] '%s' → [tool] %s(%s)",
                    agent_name, t_name, str(t_args)[:120],
                )
                try:
                    result = await filtered_map[t_name].ainvoke(t_args)
                    return tc, result
                except Exception as exc:
                    logger.error(
                        "[SubAgent] '%s' ошибка тула '%s': %s",
                        agent_name, t_name, exc,
                    )
                    return tc, exc

            pairs = await asyncio.gather(
                *[_invoke_one(tc) for tc in response.tool_calls]
            )

            for tc, raw in pairs:
                t_id = tc.get("id", f"call_{iteration}_{tc['name']}")

                if isinstance(raw, Exception):
                    content = json.dumps({"error": str(raw)}, ensure_ascii=False)
                elif isinstance(raw, dict):
                    content = fit_result(
                        raw, SUBAGENT_MAX_TOOL_MSG_CHARS,
                        serialize=lambda v: json.dumps(v, ensure_ascii=False, default=str),
                    )
                else:
                    content = clip_text(str(raw), SUBAGENT_MAX_TOOL_MSG_CHARS)

                messages.append(ToolMessage(content=content, tool_call_id=t_id))

        else:
            # for-else: max_iter исчерпан без break. 
            # Это значит, что на последней итерации агент вызвал тулзы, мы их выполнили, 
            # но цикл закончился, и агент не успел прочитать результаты!
            # Делаем ОДИН финальный вызов для синтеза ответа.
            logger.warning(
                "[SubAgent] '%s' исчерпал max_iter=%d, запрашиваем финальный ответ",
                agent_name, capped_iter,
            )
            await send("Формирование финального ответа...")

            # Используем HumanMessage, чтобы не нарушать чередование ролей
            messages.append(HumanMessage(content=(
                "ВНИМАНИЕ: Лимит шагов исчерпан. Обязательно верни текстовый ответ Оркестратору "
                "на основе уже собранных данных. Опиши, что успел узнать, а что нет. "
                "НЕ вызывай больше никаких инструментов!"
            )))

            try:
                # Обязательно вызываем с тулзами, чтобы API не ругался на историю ToolMessage
                final_resp = await llm_with_tools.ainvoke(messages)
                
                # Если упрямая модель всё равно вернула tool_calls вместо текста
                if not final_resp.content and getattr(final_resp, "tool_calls", None):
                    final_answer = f"Субагент '{agent_name}' не уложился в отведенное время."
                else:
                    final_answer = final_resp.content or f"Субагент '{agent_name}' не вернул ответ."
                    
            except Exception as e:
                logger.error("[SubAgent] '%s' ошибка финализации: %s", agent_name, e)
                final_answer = f"Ошибка финализации субагента: {e}"

        await send(f"{_STATUS_DONE} ({iteration + 1} итераций)")

        logger.info(
            "[SubAgent] '%s' завершён | ответ=%.120s",
            agent_name, final_answer,
        )
        return final_answer

    return call_subagent

