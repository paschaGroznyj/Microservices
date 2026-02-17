import logging
import json
import re
from typing import List, Dict, Any, Optional
from enum import Enum
from pydantic import BaseModel, Field
from pathlib import Path
import time
import asyncio
from langchain.chat_models.gigachat import GigaChat
from langchain.schema import SystemMessage
from core.config import *
from .vector_db import VectorDBManager
from .monitor import StorageMonitor
from core.config import ARTICLES_FOLDER, VECTOR_DB_PATH, STORAGE_PATH, GIGACHAT_CREDENTIALS, METADATA_FILE, HF_TOKEN, QWEN_CREDENTIALS
from .summary import SummaryGenerator
from .pm_parser.botocore_client import OBSWorker
from huggingface_hub import InferenceClient
import os
from .summary_period import agent_composite_digest_tool

logging.basicConfig(level=logging.INFO)


# Pydantic модели для structured output
class ActionType(str, Enum):
    SEARCH_DB = "search_db"
    SUMMARIZE_FILE = "summarize_file"
    SUMMARIZE_ALL = "summarize_all"
    SUMMARIZE_NEW = "summarize_new"
    REBUILD_ALL = "rebuild_all"
    ANALYZE_FOLDER = "analyze_folder"
    DELETE_FILE = "delete_file"
    SUMMARIZE_FILTER = 'summarize_filt'


class AgentAction(BaseModel):
    action: ActionType = Field(..., description="Действие агента")
    files: Optional[List[str]] = Field(default=None, description="Файлы")
    query: Optional[str] = Field(default=None, description="Запрос поиска")
    reasoning: str = Field(..., description="Обоснование действия", alias='reasoning')


class PydanticOutputParser:
    def __init__(self, pydantic_object):
        self._pydantic_object = pydantic_object

    def parse(self, text: str) -> AgentAction:
        """НАЙДЕТ JSON в любом месте текста (даже после схемы + объяснений)"""
        try:
            # 1. Убираем ** markdown
            text = re.sub(r'\*\*(.*)\*\*', r'\1', text)
            text = re.sub(r'``````', '', text)
            text = re.sub(r'``````', '', text, flags=re.DOTALL)

            # 2. Ищем ВСЕ JSON блоки
            json_blocks = re.findall(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)

            if not json_blocks:
                raise ValueError("JSON блоки не найдены")

            # 3. Берем ПОСЛЕДНИЙ JSON (тот, что с реальным ответом)
            last_json = json_blocks[-1]

            # print(f"Найден JSON: {last_json[:100]}...")

            # 4. Парсим
            data = json.loads(last_json)
            if 'action' in data:
                # Приводим к правильным значениям enum
                action_map = {
                    'search_db': 'search_db',
                    'summarize_file': 'summarize_file',
                    'summarize_all': 'summarize_all',
                    'summarize_new': 'summarize_new',
                    'rebuild_all': 'rebuild_all',
                    'analyze_folder': 'analyze_folder',
                    'delete_file': 'delete_file',
                    'summarize_filt': 'summarize_filt',
                    # Fallback для опечаток
                    'search': 'search_db',
                    'summary': 'summarize_file',
                    'summarize': 'summarize_file',
                    'rebuild': 'rebuild_all',
                    'build': 'rebuild_all',
                    'analyze': 'analyze_folder',
                    'summarize_filter': 'summarize_filt'
                }
                data['action'] = action_map.get(data['action'].lower().strip(), data['action'])
            return self._pydantic_object.model_validate(data)

        except json.JSONDecodeError as e:
            raise Exception(f"JSON ошибка: {e}\nТекст: {text[:200]}...")
        except Exception as e:
            raise self._fallback_parse(text)

    def _fallback_parse(self, text: str) -> AgentAction:
        """РЕЗЕРВНЫЙ ПАРСЕР - если Pydantic упал"""
        print("Используем FALLBACK парсер")

        text_lower = text.lower()
        action_map = {
            'search_db': ['search', 'поиск', 'найди', 'найти'],
            'summarize_file': ['summarize_file', 'summarize', 'суммаризуй', 'дайджест', 'кратко'],
            'summarize_all': ['summarize_all', 'всех', 'все статьи', 'дайджест всех'],
            'summarize_new': ['summarize_new', 'новые', 'все новые'],
            'rebuild_all': ['rebuild_all', 'построй', 'перестрой', 'обнови', 'создай базу'],
            'analyze_folder': ['analyze_folder', 'проверь папку', 'анализ', 'анализируй'],
            'delete_file': ['delete_file', 'удали', 'все новые'],
            'summarize_filt': ['по категории', 'за период'],
        }

        # Определяем action по ключевым словам
        for action, keywords in action_map.items():
            if any(keyword in text_lower for keyword in keywords):
                return AgentAction(
                    action=action,
                    files=[],
                    query="",
                    reasoning=f"Fallback: найдены ключевые слова для {action}"
                )

        # Последний резерв - поиск по запросу
        return AgentAction(
            action="analyze_folder",
            files=[],
            query="",
            reasoning="Fallback: анализ по умолчанию"
        )

    @property
    def format_instructions(self) -> str:
        """ПРОСТАЯ инструкция БЕЗ Pydantic схемы"""
        return """В КОНЦЕ ОТВЕТА добавь ТОЧНО такой JSON:

**{
  "action": "search_db|summarize_file|summarize_all|summarize_new|rebuild_all|analyze_folder|delete_file|summarize_filt",
  "query": "поисковый запрос", 
  "files": ["file1.md"],
  "reasoning": "почему выбрал этот инструмент"
}**

НЕ добавляй схемы или примеры JSON перед этим!"""


# ========== АВТОМАТИЧЕСКИЙ ОБРАБОТЧИК НОВЫХ СТАТЕЙ ==========
class AutoProcessor:
    """Автоматическая обработка новых статей после парсера"""

    def __init__(self, articles_path: Path, monitor: StorageMonitor,
                 vector_db: VectorDBManager, summarizer: SummaryGenerator,
                 dedup_manager=None):
        self.articles_path = articles_path
        self.monitor = monitor
        self.vector_db = vector_db
        self.summarizer = summarizer
        base_dir = Path(__file__).resolve().parent  # final_agent
        self.name_meta_dir = base_dir / Path(STORAGE_PATH)

    def process_new_articles(self) -> Dict[str, Any]:
        """ГЛАВНАЯ ФУНКЦИЯ: Авто-обработка новых статей"""

        # ШАГ 1: Находим новые статьи
        changes = self.monitor.get_changes()
        new_articles = changes.get('new', [])
        deleted_files = changes.get('deleted', [])

        print("Удаленные файлы=============", deleted_files)

        results = {"new": [], "deleted": []}

        # НОВЫЕ ФАЙЛЫ → Дайджесты
        if new_articles:
            print(f"Новых статей: {len(new_articles)}")
            for filename in new_articles:
                filepath = self.articles_path / filename
                if filepath.exists():
                    content = filepath.read_text(encoding='utf-8')
                    json_file = self.name_meta_dir / "metadata.json"
                    digest_result = self.summarizer.generate_summary(content, filename, json_file=json_file)
                    results["new"].append({
                        "file": filename,
                        "status": digest_result["status"],
                        "digest_file": digest_result.get('digest_file', '')
                    })

        # УДАЛЕННЫЕ ФАЙЛЫ → Удаление из БД
        if deleted_files:
            print(f"Удаленных: {len(deleted_files)}")
            self.vector_db.delete_documents(deleted_files)
            results["deleted"] = deleted_files

        # ПЕРЕСТРОЙКА БД (если были изменения)
        if new_articles or deleted_files:
            print("Перестраиваем БД...")
            vector_count = self.vector_db.rebuild_index()
            results["vectors_built"] = vector_count

        return results


class KnowledgeAgent:
    def __init__(self, skip_startup_autoprocess: bool = False):
        base_dir = Path(__file__).resolve().parent  # final_agent
        self.articles_path = base_dir / Path(STORAGE_PATH) / ARTICLES_FOLDER
        self.name_meta_dir = base_dir / Path(STORAGE_PATH)
        self.digest_path = base_dir / "digests" # Хардкод, занести в конфиг
        # print("ПУТЬ ДО БАЗЫ ===========", self.articles_path)
        self.monitor = StorageMonitor(STORAGE_PATH, METADATA_FILE)
        self.vector_db = VectorDBManager(str(self.digest_path), VECTOR_DB_PATH)
        self.summarizer = SummaryGenerator()
        self.auto_processor = AutoProcessor(
            self.articles_path, self.monitor, self.vector_db,
            self.summarizer)

        # PydanticOutputParser
        self.parser = PydanticOutputParser(pydantic_object=AgentAction)
        self.obs_loader = OBSWorker()

        # Флаг для автозапуска
        self.skip_startup_autoprocess = skip_startup_autoprocess

        if not skip_startup_autoprocess:
            self._startup_autoprocess()

    def _startup_autoprocess(self):
        """АВТОМАТИЧЕСКАЯ ОБРАБОТКА при запуске (после парсера)"""
        result = self.auto_processor.process_new_articles()


    def get_state(self) -> str:
        """Текущее состояние системы с проверкой существования БД"""
        changes = self.monitor.get_changes()
        stats = self.vector_db.get_stats()

        # Проверяем наличие векторной базы
        db_exists = stats['total_vectors'] > 0
        db_status = "СУЩЕСТВУЕТ" if db_exists else "ОТСУТСТВУЕТ - НУЖНО ПОСТРОИТЬ"

        return f"""Статистика:
    • Векторов: {stats['total_vectors']} {db_status}
    • Новых файлов: {len(changes['new'])}
    • Изменённых: {len(changes['modified'])}
    • Удалённых: {len(changes['deleted'])}"""

    def _analyze(self, user_input: str) -> Dict[str, Any]:
        """АНАЛИЗ с авто-рекомендацией построения БД"""
        state = self.get_state()
        changes = self.monitor.get_changes()

        # УМНЫЙ промпт с авто-постройкой БД
        prompt = f"""Ты ИИ-агент по управлению базой знаний.

    ДОСТУПНЫЕ ИНСТРУМЕНТЫ:
    1. [search_db] - найти документы по запросу  
    2. [summarize_file] - суммаризация/ревью файла
    3. [summarize_new] - суммаризация/ревью новых статей
    4. [summarize_all] - ревью ВСЕХ файлов
    5. [rebuild_all] - ПОЛНАЯ перестройка БД
    6. [analyze_folder] - анализ папки
    7. [delete_file] - удалить файл из БД
    8. [summarize_filt] - суммаризация/дайджест с условием по дате или теме

    ОСОБЫЕ ПРАВИЛА:
    - Если БД ОТСУТСТВУЕТ (0 векторов) → ВСЕГДА выбирай [rebuild_all]
    - Есть НОВЫЕ/ИЗМЕНЁННЫЕ файлы → [rebuild_all]
    - "ревью по статье", "суммаризуй", "кратко" → [summarize_file]
    - "ревью по всем", "суммаризуй все" → [summarize_all]
    - Если запрос на поиск → [search_db]
    - "проверь папку", "анализ" → [analyze_folder]
    - "удалить файл" → [delete_file]
    - "дайджест по категории", "дайджест за период" → [summarize_filt]

    Состояние: {state}
    Изменения: Новых={len(changes['new'])}, Изменённых={len(changes['modified'])}

    Запрос: {user_input}

    {self.parser.format_instructions}

    ШАГ 1: Проверь наличие БД и изменения
    ШАГ 2: Проанализируй запрос  
    ШАГ 3: Выбери ТОЧНЫЙ инструмент"""

        try:
            gigachat = GigaChat(
                credentials=GIGACHAT_CREDENTIALS,  # Обязательный параметр
                verify_ssl_certs=False,  # Отключаем проверку SSL
            )
            messages = [
                SystemMessage(
                    content=prompt
                )
            ]
            response = gigachat(messages)

            # Парсим план агента
            for attempt in range(3):
                try:
                    action = self.parser.parse(response.content)

                    print("ПАРСИНГ УСПЕШЕН:", action)
                    break
                except Exception as parse_error:
                    print(f"Попытка {attempt + 1}/3: {parse_error}")
                    if attempt == 2:
                        raise parse_error

            return {
                "user_input": user_input,
                "state": state,
                "reasoning": action.reasoning,
                "action": action.action,
                "files": action.files or [],
                "query": action.query or "",
                "status": "analyzed",
                "db_exists": self.vector_db.get_stats()['total_vectors'] > 0
            }

        except Exception as e:
            print(f"Ошибка анализа: {e}")
            return self._fallback_user_action(user_input)

    def _fallback_user_action(self, user_input: str) -> Dict[str, Any]:
        user_lower = user_input.lower()
        if any(word in user_lower for word in ['поиск', 'найди', 'search']):
            return {"action": "search_db", "query": user_input, "files": [], "reasoning": "Поиск"}
        elif any(word in user_lower for word in ['дайджест', 'суммаризуй', 'кратко']):
            return {"action": "summarize_file", "files": [], "query": "", "reasoning": "Дайджест"}
        elif 'удали' in user_lower or 'delete' in user_lower:
            return {"action": "delete_file", "files": [], "query": "", "reasoning": "Удаление"}
        elif any(word in user_lower for word in ['обнови', 'построй', 'перестрой']):
            return {"action": "rebuild_all", "files": [], "query": "", "reasoning": "Перестройка"}
        return {"action": "analyze_folder", "files": [], "query": "", "reasoning": "Анализ"}

    def pluralize_vectors(self, n: int) -> str:
        if n % 10 == 1 and n % 100 != 11:
            return "вектор"
        elif 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
            return "вектора"
        else:
            return "векторов"

    def _execute(self, action: AgentAction, user_input='') -> Dict[str, Any]:
        """ШАГ 2: ВЫПОЛНЕНИЕ ИНСТРУМЕНТА"""
        # print(f"ВЫПОЛНЯЕМ: {action['action']}")

        try:
            if action['action'] == "rebuild_all":
                changes = self.monitor.get_changes()
                deleted = changes.get('deleted', [])
                if deleted:
                    self.vector_db.delete_documents(deleted)
                count = self.vector_db.rebuild_index()
                word = self.pluralize_vectors(count)
                return {"status": "success", "message": f"Я построил {count} {word}"}

            elif action['action'] == "delete_file":
                files = action.get('files', [])
                if files:
                    self.vector_db.delete_documents(files)
                    return {"status": "success", "message": f"Я удалил {len(files)} файлов"}
                return {"status": "error", "message": "Нет файлов для удаления"}

            elif action['action'] == "analyze_folder":
                changes = self.monitor.get_changes()
                files_list = list(self.articles_path.glob("*.md"))
                return {
                    "status": "success",
                    "new_files": len(changes['new']),
                    "modified_files": len(changes['modified']),
                    "total_files": len(files_list),
                    "files": [f.name for f in files_list[:5]],
                    "message": "Я проанализировал указанную тобой директорию"
                }

            elif action['action'] == "search_db":
                results = self.vector_db.similarity_search(action['query'] or "", k=5)
                if not results:
                    return {
                        "status": "success",
                        "count": 0,
                        "preview": [],
                        "message": "По вашему запросу ничего не найдено в базе знаний."
                    }

                formatted_results = []
                for i, r in enumerate(results, 1):
                    content_snippet = r['content'].strip()
                    if len(content_snippet) > 300:
                        content_snippet = content_snippet[:300].rsplit(' ', 1)[0] + '…'
                    score = r['score']

                    block = f"**Результат {i}** (релевантность: {score:.3f})\n" \
                            f"**Фрагмент**: {content_snippet}"
                    formatted_results.append(block)

                full_preview = "\n---\n".join(formatted_results)
                message = f"Найдено {len(results)} релевантных фрагментов по запросу:\n\n{full_preview}"

                return {
                    "status": "success",
                    "count": len(results),
                    "preview": formatted_results,
                    "message": message
                }

            elif action['action'] in ["summarize_file", "summarize_all", "summarize_new", "summarize_filt"]:
                if action['action'] == "summarize_all":
                    asyncio.run(self.obs_loader.run_download())
                    # Дайджест ВСЕХ статей
                    md_files = list(self.articles_path.glob("*.md"))
                    results = []
                    # print("Спискок статей", len(md_files))
                    for filepath in md_files:
                        json_file = self.name_meta_dir / "metadata.json"
                        digest_result = self.summarizer.generate_summary(
                            filepath.read_text(encoding='utf-8'),
                            filepath.name,
                            json_file
                        )
                        results.append({
                            "file": filepath.name,
                            "status": digest_result["status"],
                            "digest_file": digest_result.get('digest_file')
                        })

                    asyncio.run(self.obs_loader.upload_digests())

                    success_count = sum(1 for r in results if r["status"] == "created")
                    return {
                        "status": "success",
                        "message": f"Я сформировал Дайджесты: {success_count}/{len(results)}. Их можно найти по пути obs://cds-bucket/reviews/",
                        "results": results
                    }
                elif action['action'] == "summarize_new":
                    asyncio.run(self.obs_loader.run_download())
                    results = self.auto_processor.process_new_articles()
                    asyncio.run(self.obs_loader.upload_digests())
                    asyncio.run(self.obs_loader.upload_vectorstore())
                    return {
                        "status": "success",
                        "message": f"Провел саммари новых статей",
                        "results": results
                    }
                elif action['action'] == "summarize_filt":
                    client = InferenceClient(api_key=HF_TOKEN)
                    json_file = self.name_meta_dir / "metadata.json"
                    results = agent_composite_digest_tool(query=user_input, llm_client=client, json_path=json_file, md_folder=self.digest_path)
                    filename_fin = results['digest_file']
                    # print(results['digest_file'])
                    # print(results['filename'])
                    if results['found_articles'] == 0:
                        message = "Материалов за указанный вами период или категории нет, выберите другой период или категорию"
                    else:
                        message = f"Я сформировал дайджест по {results['found_articles']} статьям, найти его можно по пути: obs://cds-bucket/digests/{results['filename']}"
                    # print(message)
                    asyncio.run(self.obs_loader.upload_single_digest_cond(filename_fin, results['filename']))
                    return {
                        "status": "success",
                        "message": message,
                        "results": results
                    }
                else:  # summarize_file
                    if action.get('files') and action['files'][0]:
                        filename = action.get('files', [None])[0]

                        asyncio.run(self.obs_loader.ensure_article_downloaded(filename))
                        filepath = self.articles_path / filename
                        if filepath.exists():
                            content = filepath.read_text(encoding='utf-8')
                            json_file = self.name_meta_dir / "metadata.json"
                            digest_result = self.summarizer.generate_summary(content, filename, json_file)

                            if digest_result["status"] == "created":
                                filename_fin = digest_result['digest_file']
                                asyncio.run(self.obs_loader.upload_single_digest(filename_fin, filename))
                                return {
                                    "status": "success",
                                    "message": f"Я сформировал одиночный дайджест по файлу {digest_result['digest_file']}. Его можно найти по пути: obs://cds-bucket/reviews/",
                                    "summary": digest_result.get("digest")
                                }
                            elif digest_result["status"] == "exists":
                                filename_fin = digest_result['digest_file']
                                asyncio.run(self.obs_loader.upload_single_digest(filename_fin, filename))
                                return {
                                    "status": "exists",
                                    "message": f"Дайджест по файлу уже есть: {digest_result['digest_file']}. Расположен по пути: obs://cds-bucket/reviews/"
                                }

                    return {"status": "error", "message": "Не получилось найти файл"}

        except Exception as e:
            return {"status": "error", "message": f"Ошибка: {str(e)}"}

    def run_autonomous_mode(self) -> Dict[str, Any]:
        """АВТОНОМНЫЙ РЕЖИМ: только автозапуск + 30мин ожидания пользователя"""

        start_time = time.time()

        try:
            # ШАГ 1: Автоматическая обработка новых статей
            result = self.auto_processor.process_new_articles()

            # ШАГ 2: Финальное состояние
            final_stats = self.get_state()


            return {
                "mode": "autonomous",
                "status": "completed",
                "autoprocess": result,
                "duration": time.time() - start_time,
                "final_stats": final_stats
            }

        except Exception as e:
            return {"mode": "autonomous", "status": "error", "error": str(e)}

    def process_query(self, user_input: str) -> Dict[str, Any]:
        try:
            action = self._analyze(user_input)
            result = self._execute(action, user_input)
            final_state = self.get_state()

            user_message = result.get("message", "Операция завершена.")
            return {
                "input": user_input,
                "action": action["action"],
                "message": user_message,
                "reasoning": action["reasoning"],
                "result": result,
                "final_state": final_state,
                "success": True
            }
        except Exception as e:
            error_msg = f"Я упал, но скоро встану. Ошибка: {str(e[:100])}"
            return {
                "input": user_input,
                "error": str(e),
                "success": False,
                "message": error_msg
            }


