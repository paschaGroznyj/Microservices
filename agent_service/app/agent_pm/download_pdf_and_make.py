import httpx
import asyncio
from io import BytesIO
from PyPDF2 import PdfReader
from langchain.schema import HumanMessage, SystemMessage

# Ограничитель сетевых запросов (чтобы не положить ArXiv и свой канал)
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(3)


async def download_and_parse_pdf(link: str) -> str:
    """
    Асинхронно скачивает PDF и извлекает текст с первых 4 страниц.
    Использует семафор для контроля параллелизма.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/91.0.4472.124 Safari/537.36"
    }

    # Ждем слота в семафоре перед началом скачивания
    async with DOWNLOAD_SEMAPHORE:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(link, headers=headers)
                response.raise_for_status()

                pdf_bytes = response.content

                # Парсинг (CPU-bound, но для 4 стр. быстро)
                reader = PdfReader(BytesIO(pdf_bytes))
                text_parts = []

                pages_count = min(len(reader.pages), 4)
                for i in range(pages_count):
                    page_text = reader.pages[i].extract_text()
                    if page_text:
                        text_parts.append(page_text)

                full_text = "\n".join(text_parts)

                # Небольшая задержка между запросами к одному домену
                await asyncio.sleep(1)

                return full_text

        except Exception as e:
            raise Exception(f"Download/Parsing error for {link}: {str(e)}")


async def generate_review(llm, content: str, title: str, link: str):
    SYSTEM_PROMPT = f"""Твоя задача: прочитать текст статьи из "{title}", извлечь и адаптировать информацию для русскоязычного эксперта.

    ## ПРАВИЛА (строго соблюдай):
    1. Переводи для русскоязычного эксперта, сохраняя академический стиль, , сохраняй точные термины в оригинале, например 'process mining' или названия инструментов: 'SOUP: Simplifying Event...' оставляй как есть в оригинале
    2. Факты БЕРИ ТОЛЬКО ИЗ ТЕКСТА, без домыслов
    3. Первый раз термин сокращенно: на original языке. Далее: термин на оригинальном языке, перевод и расшифровки на русском
    4. Алгоритмы - пошагово с оригинальными названиями
    5. GitHub: найди в тексте ИЛИ проанализируй авторов/метод, иначе 'отсутствует'
    6. Выводи результат строго по шаблону, без дополнительных комментариев и пояснений
    7. Не меняй порядок пунктов шаблона и не добавляй новые
    8. Не переводи авторов, оставляй в оригинале


    ## Пример шаблона саммари:
    "Название статьи на русском:",
    "Название оригинальной статьи на английском:",
    "Авторы на английском (в оригинале):",
    "Ссылка на источник статьи: {link}"
    "Описание":
        1. **Цель:** ...
        2. **Проблема:** ...
        3. **Задачи:** ...
        4. **Подход - суть алгоритма/метода:** ...
        5. **Как реализуется подход:** ...
        6. **Пример из статьи:** ...
        7. **Вывод:** ...

    ## Текст статьи из PDF:
    """

    try:
        # Обрезаем контент, чтобы не превысить контекстное окно (например, 3000 символов)
        # Для лучших результатов лучше использовать chunking, но для краткости оставим так
        safe_content = content[:3000] if content else ""

        response = llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=safe_content)
        ])

        summary = response.content.strip()
        print(f"✨ Review generated for: {title[:30]}...")
        return summary

    except Exception as e:
        print(f"LLM Error for {title}: {e}")
        return f"Ошибка генерации ревью: {str(e)}"
