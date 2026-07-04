import os
import json
import time
import shutil
import requests
from io import BytesIO
from PyPDF2 import PdfReader
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dotenv import load_dotenv
from langchain.schema import SystemMessage, HumanMessage
from .core.agent_provider import get_async_gemini
from .analyze_article import RelevanceAnalyzer

load_dotenv()

gigachat = get_async_gemini()

class MetadataUpdater:
    def __init__(self, metadata_file: Path, articles_path: Path, max_retries: int = 3, retry_delay: int = 10):
        self.metadata_file = metadata_file
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.articles_path = articles_path
        # self.llm = InferenceClient(api_key=HF_TOKEN)
        
        # self.llm = GigaChat(
        #     credentials=GIGACHAT_CREDENTIALS,  # Обязательный параметр
        #     verify_ssl_certs=False,  # Отключаем проверку SSL
        # )

        # Список допустимых категорий (для валидации, можно вынести в конфиг)
        self.valid_categories = [
            "Общие принципы: определение PM",
            "Общие принципы: Стандарты PM",
            "Общие принципы: Фреймворки PM",
            "Общие принципы: Моделирование в PM",
            "Общие принципы: Неэффективности",
            "Методики исследования: CRISP_DM",
            "Методики исследования: PM Square",
            "PM-лог: Структура логов",
            "PM-лог: Форматы данных",
            "Проверка качества: Качество данных",
            "Проверка качества: Качество лога"
        ]

        self.analyzer = RelevanceAnalyzer(gigachat)
        self._http_timeout = 30
        self._http_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            )
        }

    async def assess_relevance_and_category(self, item: Dict) -> Dict[str, Any]:
        """
        Оценивает релевантность статьи и возвращает категорию + метрики
        """
        file_name = item.get('file_article', 'Без названия')
        abstract = item.get('abstract', '')
        
        if not abstract:
            return {
                "is_relevant": False,
                "overall_score": 0.0,
                "category": None,
                "reasoning": "Нет абстракта для анализа"
            }
        
        try:
            # Анализируем релевантность по тексту абстракта
            result = await self.analyzer.assess_relevance(
                content=abstract, 
                filename=file_name
            )
            
            # Если не релевантна - пропускаем категоризацию
            if not result["is_relevant"]:
                return {
                    "is_relevant": False,
                    "overall_score": result["overall_score"],
                    "category": None,
                    "reasoning": result["reasoning"]
                }
            
            # Если релевантна - определяем категорию
            category = await self.get_category(abstract)
            return {
                "is_relevant": True,
                "overall_score": result["overall_score"],
                "category": category,
                "reasoning": result["reasoning"]
            }
            
        except Exception as e:
            return {
                "is_relevant": False,
                "overall_score": 0.0,
                "category": None,
                "reasoning": f"Ошибка анализа: {str(e)}"
            }

    async def get_category(self, content: str) -> Optional[str]:
        """Запрашивает категорию у LLM с базовой обработкой ответа."""
        categories_str = "\n".join(f"- {cat}" for cat in self.valid_categories)

        # Ограничиваем контент, чтобы не тратить токены
        prompt = f"""Выбери СТРОГО ОДНУ категорию из списка. 
Категории: {categories_str}
Текст: [text]
Ответ только названием категории."""

        try:
            # response = self.llm.chat.completions.create(
            #     model="Qwen/Qwen3.5-397B-A17B",
            #     max_tokens=1000,
            #     messages=[{"role": "user", "content": prompt}],
            #     temperature=0.5,
            #     top_p=0.95
            # )
            #
            # if not response.choices or not response.choices[0].message.content:
            #     return None
            #
            # raw_content = response.choices[0].message.content.strip().strip('"\'')
            # message = [
            #     SystemMessage(
            #         content=prompt
            #     )
            # ]
            messages = [SystemMessage(content=prompt)]
            response_sum = await gigachat.ainvoke([
            SystemMessage(content=prompt),
            HumanMessage(content=content[:1500]),
        ])
            # response_sum = self.llm(message)
            raw_content = response_sum.content

            # Точное совпадение
            if raw_content in self.valid_categories:
                return raw_content

            # Частичное совпадение (на случай мусора в ответе)
            for cat in self.valid_categories:
                if cat in raw_content:
                    return cat

            return None

        except Exception as e:
            print(f"Ошибка запроса к LLM: {e}")
            raise e  # Пробрасываем ошибку выше, чтобы обработать её в цикле retry

    async def run(self, rebuild_errors: bool = False):
        if not self.metadata_file.is_file():
            return "Файл с метаданными не найден."

        try:
            with open(self.metadata_file, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
        except json.JSONDecodeError:
            return "Ошибка: Файл metadata.json поврежден."
        self._retry_failed_pdfs(metadata)

        # Находим элементы без категории или с ошибкой анализа
        def _has_analysis_error(item: Dict) -> bool:
            reasoning = item.get('relevance_reasoning', '')
            return 'Ошибка анализа' in reasoning

        if rebuild_errors:
            items_to_process = [
                item for item in metadata
                if (_has_analysis_error(item) or item.get('category') is None)
                and item.get('abstract')
            ]
        else:
            items_to_process = [
                item for item in metadata
                if item.get('category') is None and item.get('abstract')
            ]

        if not items_to_process:
            return "Все записи уже имеют категории."

        print(f"Найдено {len(items_to_process)} записей для обработки.")

        # Основной цикл повторных попыток
        for attempt in range(1, self.max_retries + 1):
            print(f"\n--- Попытка {attempt} из {self.max_retries} ---")

            processed_in_this_batch = 0
            failed_items = []
            relevant_count = 0

            for i, item in enumerate(items_to_process):
                title = item.get('title', 'Без названия')[:40]
                try:
                    analysis = await self.assess_relevance_and_category(item)
                    if analysis["is_relevant"]:
                        item['category'] = analysis["category"]
                        item['relevance_score'] = analysis["overall_score"]
                        item['relevance_reasoning'] = analysis["reasoning"][:200]
                        item['is_relevant'] = analysis['is_relevant']
                        relevant_count += 1
                        processed_in_this_batch += 1

                        # ← Качаем PDF только для релевантных
                        pdf_url = item.get('pdf_url', '')
                        if pdf_url:
                            text, pdf_bytes = self._fetch_pdf_text(pdf_url)
                            # item['text_article'] = text

                            if pdf_bytes:
                                pdf_path = self._save_pdf(item, pdf_bytes)
                                item['file_article'] = str(pdf_path) if pdf_path else item.get('file_article', '')

                        status = f"[✅] {title} -> {analysis['category']} (score: {analysis['overall_score']})"
                    else:
                        item['category'] = "Не релевантна PM"
                        item['relevance_score'] = analysis["overall_score"]
                        item['relevance_reasoning'] = analysis["reasoning"][:100]
                        item['is_relevant'] = False
                        status = f"[⚠️] {title} -> score {analysis['overall_score']:.1f}"

                    print(status)

                except Exception as e:
                    print(f"[ERR] {title} -> {str(e)[:50]}...")
                    failed_items.append(item)

            self._save_metadata(metadata)

            print(f"Обработано успешно: {processed_in_this_batch}. Релевантно {relevant_count}. Ошибок/Пропусков: {len(failed_items)}")

            # Если все обработали успешно — выходим
            if not failed_items:
                return f"Успешно! Обработано {processed_in_this_batch} записей."

            # Если остались ошибки и есть еще попытки — ждем и повторяем ТОЛЬКО для ошибок
            if attempt < self.max_retries:
                print(f"Ждем {self.retry_delay} сек перед следующей попыткой для {len(failed_items)} элементов...")
                time.sleep(self.retry_delay)
                items_to_process = failed_items
            else:
                return f"Завершено. Не удалось обработать {len(failed_items)} записей после {self.max_retries} попыток."

        return "Завершено."

    def _retry_failed_pdfs(self, metadata: list):
        """Перекачивает PDF для релевантных статей у которых нет файла на диске или флаг pdf_downloaded=False."""

        # Читаем что реально лежит в директории
        existing_md = set()
        if self.articles_path.exists():
            existing_md = {f.stem for f in self.articles_path.glob("*.md")}

        failed = []
        for item in metadata:
            if not item.get('is_relevant'):
                continue

            # Проверка 1: флаг
            flag_ok = item.get('pdf_downloaded', False)

            # Проверка 2: файловая система
            file_article = item.get('file_article', '')
            expected_stem = Path(file_article).stem if file_article else None
            file_ok = expected_stem in existing_md if expected_stem else False

            # Нужна дозакачка если хотя бы одна проверка не прошла
            if not flag_ok or not file_ok:
                reason = []
                if not flag_ok:
                    reason.append("флаг=False")
                if not file_ok:
                    reason.append("файл не найден")
                print(f"  [~] {item.get('title', '')[:40]} → {', '.join(reason)}")
                failed.append(item)

        if not failed:
            print("Все PDF на месте, дозакачка не нужна.")
            return

        print(f"Дозакачка PDF для {len(failed)} статей...")
        retried = 0

        for item in failed:
            pdf_url = item.get('pdf_url', '')
            if not pdf_url:
                continue

            text, pdf_bytes = self._fetch_pdf_text(pdf_url)
            if pdf_bytes:
                item['pdf_downloaded'] = True  # ← обновляем флаг
                pdf_path = self._save_pdf(item, pdf_bytes)
                if pdf_path:
                    item['file_article'] = str(pdf_path.name)
                retried += 1
                print(f"  [✅] Дозакачан: {item.get('title', '')[:40]}")
            else:
                item['pdf_downloaded'] = False  # ← явно фиксируем что снова не удалось
                print(f"  [❌] Снова не удалось: {item.get('title', '')[:40]}")

        if retried:
            self._save_metadata(metadata)
            print(f"Дозакачано {retried} из {len(failed)}")

    def _fetch_pdf_text(self, pdf_url: str, max_pages: int = 4) -> Tuple[str, bytes]:
        """Скачивает PDF и извлекает текст первых max_pages страниц."""
        try:
            response = requests.get(
                pdf_url,
                headers=self._http_headers,
                timeout=self._http_timeout,
            )
            response.raise_for_status()
            pdf_bytes = response.content
            reader = PdfReader(BytesIO(pdf_bytes))
            text = "".join(
                page.extract_text() or ""
                for page in reader.pages[:max_pages]
            )
            time.sleep(3)  # вежливая пауза — как в ARXIVParser
            return text, pdf_bytes
        except Exception as exc:
            print("Не удалось скачать PDF %s: %s", pdf_url, exc)
            return "", b""

    def _save_pdf(self, item: Dict, pdf_bytes: bytes) -> Optional[Path]:
        """Извлекает текст из PDF и сохраняет как .md файл."""
        try:
            file_article = item.get('file_article', '')
            if file_article:
                md_name = Path(file_article).stem + ".md"
            else:
                import hashlib
                url_hash = hashlib.sha256(item.get('title', '').encode()).hexdigest()[:12]
                md_name = f"{item.get('date_sub', 'unknown')}_{url_hash}.md"

            md_path = self.articles_path / md_name
            self.articles_path.mkdir(parents=True, exist_ok=True)

            # Извлекаем текст из PDF байтов
            reader = PdfReader(BytesIO(pdf_bytes))
            text = "".join(
                page.extract_text() or ""
                for page in reader.pages[:4]
            )

            # Сохраняем как текст, не бинарник
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(text)

            print(f"    💾 MD сохранён: {md_path.name}")
            return md_path

        except Exception as e:
            print(f"    [ERR] Не удалось сохранить MD: {e}")
            return None

    def _save_metadata(self, metadata: List[Dict]):
        """Атомарное сохранение файла."""
        temp_file = self.metadata_file.with_suffix('.tmp')
        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
            shutil.move(str(temp_file), str(self.metadata_file))
        except Exception as e:
            if temp_file.exists():
                temp_file.unlink()
            print(f"Критическая ошибка сохранения: {e}")
            raise e


# # Использование
# if __name__ == "__main__":
#     base_dir = Path(__file__).resolve().parent
#     json_file = base_dir / "knowledge_base" / "metadata.json"

#     # Создаем экземпляр класса
#     updater = MetadataUpdater(
#         metadata_file=json_file,
#         max_retries=3,  # Количество подходов
#         retry_delay=15  # Пауза между подходами (увеличил до 15 сек для 402 ошибок)
#     )

#     result = updater.run()
#     print(result)

