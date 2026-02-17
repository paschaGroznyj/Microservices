import os
import json
import time
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from huggingface_hub import InferenceClient
from huggingface_hub.errors import HfHubHTTPError

load_dotenv()

HF_TOKEN = os.getenv("HF_TOKEN")


class MetadataUpdater:
    def __init__(self, metadata_file: Path, max_retries: int = 3, retry_delay: int = 10):
        self.metadata_file = metadata_file
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.llm = InferenceClient(api_key=HF_TOKEN)

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

    def get_category(self, content: str) -> Optional[str]:
        """Запрашивает категорию у LLM с базовой обработкой ответа."""
        categories_str = "\n".join(f"- {cat}" for cat in self.valid_categories)

        # Ограничиваем контент, чтобы не тратить токены
        prompt = f"""Выбери СТРОГО ОДНУ категорию из списка. 
Категории: {categories_str}
Текст: {content[:1500]}
Ответ только названием категории."""

        try:
            response = self.llm.chat.completions.create(
                model="Qwen/Qwen3-VL-8B-Instruct",  # Или ваша модель
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=100
            )

            if not response.choices or not response.choices[0].message.content:
                return None

            raw_content = response.choices[0].message.content.strip().strip('"\'')

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

    def run(self):
        if not self.metadata_file.is_file():
            return "Файл с метаданными не найден."

        try:
            with open(self.metadata_file, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
        except json.JSONDecodeError:
            return "Ошибка: Файл metadata.json поврежден."

        # Находим элементы без категории
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
            failed_items = []  # Сюда соберем те, что не удалось обработать

            for i, item in enumerate(items_to_process):
                title = item.get('title', 'Без названия')[:40]
                try:
                    category = self.get_category(item['abstract'])

                    if category:
                        item['category'] = category
                        processed_in_this_batch += 1
                        print(f"[OK] {title} -> {category}")
                    else:
                        # Модель вернула мусор или пустоту (не ошибка сети, а логическая)
                        # Считаем это ошибкой обработки, но не сетевой. Повторять нет смысла, если модель тупит.
                        # Но можно добавить в failed_items, если думаем, что следующий запрос пройдет.
                        print(f"[WARN] {title} -> Неверный формат ответа")
                        failed_items.append(item)

                except Exception as e:
                    # Ошибка сети или API (например, 402, таймаут)
                    print(f"[ERR] {title} -> {str(e)[:50]}...")
                    failed_items.append(item)

            # Сохраняем прогресс после каждой попытки (даже если были ошибки)
            self._save_metadata(metadata)

            print(f"Обработано успешно: {processed_in_this_batch}. Ошибок/Пропусков: {len(failed_items)}")

            # Если все обработали успешно — выходим
            if not failed_items:
                return f"Успешно! Обработано {processed_in_this_batch} записей."

            # Если остались ошибки и есть еще попытки — ждем и повторяем ТОЛЬКО для ошибок
            if attempt < self.max_retries:
                print(f"Ждем {self.retry_delay} сек перед следующей попыткой для {len(failed_items)} элементов...")
                time.sleep(self.retry_delay)
                items_to_process = failed_items  # Обновляем список только для неудачников
            else:
                return f"Завершено. Не удалось обработать {len(failed_items)} записей после {self.max_retries} попыток."

        return "Завершено."

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


# Использование
if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent
    json_file = base_dir / "knowledge_base" / "metadata.json"

    # Создаем экземпляр класса
    updater = MetadataUpdater(
        metadata_file=json_file,
        max_retries=3,  # Количество подходов
        retry_delay=15  # Пауза между подходами (увеличил до 15 сек для 402 ошибок)
    )

    result = updater.run()
    print(result)

