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

from agent_pm.tool_status_messages import get_tool_status, get_tools_status

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

def make_subagent_tool(agent_instance):
    """
    Принимает экземпляр KnowledgeAgent (self).
    Возвращает @tool call_subagent, готовую к добавлению в список тулзов PMыча.

    Пример добавления в _build_tools:

        from .tools.subAgents.make_subagent import make_subagent_tool
        tools.append(make_subagent_tool(self))
    """

    agent = agent_instance  # захват self в замыкание

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
        Делегировать задачу специализированному субагенту.

        Субагент — изолированный LLM-цикл с ограниченным набором тулзов.
        Не имеет доступа к истории чата, памяти и бортовому журналу.
        Результат (строка) возвращается PMычу — тот отвечает пользователю.

        Аргументы:
            agent_name    — роль субагента. Стандартные:
                            "data_analyst", "scraper", "researcher",
                            "content_maker", "kb_manager", "code_writer".
                            Произвольное имя допустимо — тогда system_prompt обязателен.
            task          — подробное описание задачи. Чем детальнее — тем лучше.
            allowed_tools — список имён тулзов, которые субагент может вызывать.
                            Невалидные имена фильтруются тихо.
                            Пример: ["execute_python_code", "search_code_library"]
            system_prompt — системный промт субагента. Если пустой — берётся дефолтный
                            по agent_name из DEFAULT_SYSTEM_PROMPTS.
            max_iter      — максимум итераций агентного цикла (1–12, дефолт 6).
            extra_context — дополнительный контекст: результаты других субагентов,
                            промежуточные данные, уточнения.

        Возвращает:
            Финальный ответ субагента в виде строки.
            При ошибке — строку с описанием ошибки.

        Параллельный запуск:
            Вызови call_subagent несколько раз в одном батче — LangChain запустит их
            через asyncio.gather параллельно, результаты придут независимо.
            Статусы каждого субагента маркируются [agent_name]-префиксом на фронте.
        """
        # ── Получаем request_id из агента (нужен для _send_status) ──────────
        # agent.current_request_id должен быть выставлен в process_query
        # перед стартом агентного цикла.
        request_id: str = getattr(agent, "current_request_id", "") or ""

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

        # ── 2. Строим и фильтруем тулзы ──────────────────────────────────────
        try:
            all_tools = agent._build_tools(
                user_id=0,
                chat_id=getattr(agent, "current_chat_id", 0) or 0,
                user_input=task,
                selected_tools=[],
                allow_all=True,
                images=None,
                agent_model=getattr(agent, "current_agent_model", "universal"),
            )
        except Exception as e:
            logger.error("[SubAgent] Не удалось получить тулзы: %s", e)
            await send(_STATUS_ERROR)
            return f"Ошибка инициализации субагента '{agent_name}': {e}"

        allowed_set  = set(allowed_tools)
        filtered     = [t for t in all_tools if t.name in allowed_set]
        filtered_map = {t.name: t for t in filtered}

        missing = allowed_set - set(filtered_map)
        if missing:
            logger.warning(
                "[SubAgent] '%s': тулзы не найдены (проигнорированы): %s",
                agent_name, missing,
            )

        if not filtered:
            await send(_STATUS_ERROR)
            return (
                f"Для субагента '{agent_name}' не переданы валидные инструменты: "
                f"allowed_tools={sorted(allowed_set)}. "
                f"Проверьте TOOLS_SETTINGS/allowed_tools."
            )

        # ── 3. Привязываем тулзы к LLM ───────────────────────────────────────
        base_llm = (
            getattr(agent, "llm", None)
            or agent.AGENT_MODELS.get(getattr(agent, "current_agent_model", "universal"))
            or agent.AGENT_MODELS.get("universal")
            or agent.AGENT_MODELS.get("designer")
            or agent.AGENT_MODELS.get("coder")
        )
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
                elif isinstance(raw, str):
                    content = raw
                elif isinstance(raw, dict):
                    content = json.dumps(raw, ensure_ascii=False, default=str)
                else:
                    content = str(raw)

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

