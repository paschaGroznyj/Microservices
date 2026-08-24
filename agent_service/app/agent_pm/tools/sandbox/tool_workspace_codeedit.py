
import os
import httpx
from langchain.tools import tool

SANDBOX_URL = os.environ.get("SANDBOX_SERVICE_URL", "http://agent_sandbox:8005")

# Лимиты — согласованы с фабрикой
MAX_READ_CONTENT = 200_000   # сколько текста максимум тащить в контекст модели


def make_workspace_codeedit_tools(agent):

    @tool
    async def read_workspace_file(
        workspace_id: str,
        path: str,
        offset: int = 0,
        limit: int = 0,
    ) -> str:
        """
        Читает текстовый файл (код/конфиг) ВНУТРИ workspace на фабрике.

        В отличие от read_file (который читает локальную ФС агента /app и /sandbox),
        этот тул тянет файл из persistent workspace фабрики по HTTP.

        Аргументы:
            workspace_id: ID workspace (из create_workspace)
            path:         относительный путь внутри workspace, напр.
                          "src/app.py" или "static/js/fluid.js"
            offset:       с какой строки читать (0 = с начала)
            limit:        сколько строк прочитать (0 = до конца).
                          Для больших файлов читай окном: offset=0 limit=200,
                          посмотри total_lines, потом offset=200 limit=200 и т.д.

        Возвращает текст файла с метаданными (total_lines, shown_lines).
        Используй перед edit_workspace_file, чтобы скопировать точный
        фрагмент для old_str.
        """
        params = {"path": path, "offset": offset, "limit": limit}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(
                    f"{SANDBOX_URL}/workspace/{workspace_id}/read",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            return str({
                "ok": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:300]}",
            })
        except httpx.ConnectError:
            return str({"ok": False, "error": "Sandbox-сервис недоступен."})
        except Exception as e:
            return str({"ok": False, "error": f"{type(e).__name__}: {e}"})

        content = data.get("content", "")
        if len(content) > MAX_READ_CONTENT:
            content = content[:MAX_READ_CONTENT] + "\n...(truncated by agent)..."
            data["content"] = content
            data["agent_truncated"] = True

        return str(data)

    @tool
    async def edit_workspace_file(
        workspace_id: str,
        path: str,
        old_str: str,
        new_str: str,
    ) -> str:
        """
        Точечно редактирует файл ВНУТРИ workspace на фабрике (find & replace).
        Сама замена выполняется НА СТОРОНЕ ФАБРИКИ атомарно — агент шлёт
        только {path, old_str, new_str}, файл целиком туда-сюда не гоняется.

        В отличие от edit_sandbox_file (локальный /sandbox), правит файл
        в persistent workspace, где живёт код для execute_python_code /
        execute_shell_commands.

        Аргументы:
            workspace_id: ID workspace
            path:         относительный путь к файлу внутри workspace
            old_str:      точный фрагмент для замены. Должен встречаться
                          РОВНО один раз — иначе фабрика вернёт ошибку 409
                          (0 вхождений или >1). При >1 добавь больше контекста.
            new_str:      текст замены. Пустая строка удаляет old_str.

        Порядок работы: сначала read_workspace_file, скопируй точный кусок
        (с отступами и пробелами) в old_str, затем edit_workspace_file.
        """
        payload = {"path": path, "old_str": old_str, "new_str": new_str}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{SANDBOX_URL}/workspace/{workspace_id}/edit",
                    json=payload,
                )
                resp.raise_for_status()
                result = resp.json()
        except httpx.HTTPStatusError as e:
            # 409 — неоднозначная/отсутствующая замена; текст detail информативен
            return str({
                "ok": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:300]}",
            })
        except httpx.ConnectError:
            return str({"ok": False, "error": "Sandbox-сервис недоступен."})
        except Exception as e:
            return str({"ok": False, "error": f"{type(e).__name__}: {e}"})

        return str(result)

    return [read_workspace_file, edit_workspace_file]
