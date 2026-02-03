import requests
from bs4 import BeautifulSoup
from PyPDF2 import PdfReader
from io import BytesIO
import asyncio
import re
from dotenv import load_dotenv
import json
import os
from pathlib import Path
from datetime import datetime
from typing import Optional
from langchain.chat_models.gigachat import GigaChat
from langchain.schema import SystemMessage
from core.config import GIGACHAT_CREDENTIALS
import time
from .botocore_client import OBSWorker
from agent_pm.summary import SummaryGenerator

load_dotenv()

# obs = OBSWorker()
class ARXIVParser:
    def __init__(self, data_range: tuple):
        self.months_dict = {
        'January': 1, 'February': 2, 'March': 3, 'April': 4,
            'May': 5, 'June': 6, 'July': 7, 'August': 8,
            'September': 9, 'October': 10, 'November': 11, 'December': 12
        }
        base_dir = Path(__file__).resolve().parent.parent  # final_agent
        self.name_doc_dir = base_dir / os.getenv("STORAGE_PATH") / os.getenv("ARTICLES_FOLDER")
        self.name_meta_dir = base_dir / os.getenv("STORAGE_PATH")
        self.name_doc_dig = base_dir / "digests"
        self.base_url = "https://arxiv.org/search/?"
        self.parameters = {"query": "+".join(["process", "mining"]),
                      "searchtype": "all",
                      "source": "header",
                      "start": ""
                      }
        # Преобразуем строки в date, если они строки
        if isinstance(data_range[0], str):
            self.data_start = datetime.strptime(data_range[0], "%Y-%m-%d").date()
            self.data_end = datetime.strptime(data_range[1], "%Y-%m-%d").date()
        else:
            self.data_start, self.data_end = data_range
        self.article_list = []
        self.parameters_string = ""
        self.__pre_convert_url()
        self.cat_url = self.base_url + self.parameters_string.strip("&")
        self.dict_need_tags = {"cs.AI": "Artificial Intelligence",
                               "cs.CY": "Computers and Society",
                               "cs.LG": "Machine Learning",
                               "cs.CV": "Computer Vision and Pattern Recognition",
                               "cs.HC": "Human-Computer Interaction"}
        self.doc_names = []
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        }
        self.llm = GigaChat(
            credentials=GIGACHAT_CREDENTIALS,  # Обязательный параметр
            verify_ssl_certs=False,  # Отключаем проверку SSL
        )
        self.summarizer = SummaryGenerator()
    def __pre_convert_url(self):
        for key, value in self.parameters.items():
            self.parameters_string += key + "=" + value + "&"
    def __convert_month(self, date_sub):
        submitted_date = date_sub.split(";")[0]
        day, month, year = submitted_date.split(" ")[1], submitted_date.split(" ")[2].strip(","), submitted_date.split(" ")[3]
        month = self.months_dict[month]

        day = day.zfill(2) # Добавление нуля вначале
        month = str(month).zfill(2)

        return f"{year}-{month}-{day}", (year, month, day)


    def parsing(self, data_range: Optional[tuple[datetime.date, datetime.date]] = None):
        start = data_range[0] if data_range else self.data_start
        end = data_range[1] if data_range else self.data_end
        result_list = []
        for i in range(0, 200, 50):
            get_url = self.cat_url + str(i)
            text = requests.get(get_url, headers=self.headers, timeout=15)
            soup = BeautifulSoup(text.content, 'html.parser')

            page_has_relevant = False

            preview_articles = [i for i in soup.find_all('li', class_="arxiv-result")]
            for art in preview_articles[:10]:
                tags_div = art.find('div', class_="tags is-inline-block").find_all('span')
                if not tags_div:
                    continue

                tags = []
                for tag in tags_div:
                    tags.append(tag.get('data-tooltip'))


                if not (set(self.dict_need_tags.values()) & set(tags)):
                    continue # Пропускаем статью без нужных тегов
                title = art.find('p', class_="title is-5 mathjax").text.strip()
                title = self.__clear_title_article(title)
                date_sub = art.find('p', class_='is-size-7').text.strip()
                url_article = art.find('a').get("href").strip()
                date_sub, tuple_data = self.__convert_month(date_sub)
                current_data = datetime.strptime(f"{tuple_data[0]}-{tuple_data[1]}-{tuple_data[2]}", '%Y-%m-%d').date()
                print(current_data)
                if current_data > end:
                    continue

                if current_data < start:
                    break

                page_has_relevant = True
                authors = art.find('p', class_='authors')
                authors_dict = {i.text: i.get('href') for i in authors.find_all('a')}
                abstract = self.__parse_article(url_article)


                p_tag = art.find('p', class_="list-title is-inline-block")
                arxiv_id = p_tag.find('a').text.split(":")[1] # достать
                pdf_link_tag = p_tag.find('a', string='pdf')
                pdf_url = None
                category = None

                if pdf_link_tag:
                    pdf_url = pdf_link_tag['href'].strip()
                    # try:
                    #     text_article = self.__download_pdf_article(pdf_url)
                    # except Exception as e:
                    #     print(f"Ошибка при скачивании PDF {pdf_url}: {e}")
                    #     text_article = None

                # category = self.__get_category(abstract)

                result_list.append({
                       "source": "https://arxiv.org",
                       "external_id": arxiv_id,
                       "title": title, # str
                       "data_sub": date_sub, # str {year}-{month}-{day}
                       "url_article": url_article, # str если есть
                       "pdf_url": pdf_url, # str если есть
                       "authors": authors_dict, # dict {"Sindhura Kumbakonam Subramanian": "ссылка_если_есть"}
                       "tags": tags, # list если есть
                       "category": category if category else None # Отработка после Гигачата
                })
                time.sleep(3)
            if not page_has_relevant:
                # Проверим: если самая первая статья на странице < start → выход
                if preview_articles:
                    first_art = preview_articles[0]
                    first_date_raw = first_art.find('p', class_='is-size-7').text.strip()
                    _, first_tuple = self.__convert_month(first_date_raw)
                    first_date = datetime.strptime(f"{first_tuple[0]}-{first_tuple[1]}-{first_tuple[2]}",
                                                   '%Y-%m-%d').date()
                    if first_date < start:
                        print("Достигнута нижняя граница дат. Парсинг завершён.")
                        break


        return result_list
                # self.__download_and_save_pdf_article(pdf_url, title, date_sub)

    def __get_category(self, content_:str):
        # Полный список категорий для промпта
        categories = [
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
        categories_str = "\n".join(f"- {cat}" for cat in categories)

        prompt2 = [SystemMessage(content=f"""Выбери одну категорию из следующего списка: 
                                            Категории: {categories_str} 
                                            Выбирай наиболее релевантную категорию к которой ты можешь отнести следующий текст:

                                            ## Текст статьи из PDF:
                                            {content_}.
                                            В ответ выводи только выбранную категорию, без лишнего текста.""")]
        
        response_theme = self.llm(prompt2)
        theme = response_theme.content.strip()

        return theme

    def __save_meta_in_json(self, new_articles: list[dict]):
        os.makedirs(self.name_meta_dir, exist_ok=True)
        path = self.name_meta_dir / "metadata.json"

        # Загружаем существующие
        existing = self.__read_existing_metadata()  # dict[arxiv_id, article]

        # Добавляем новые
        for art in new_articles:
            existing[art["external_id"]] = art

        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                list(existing.values()),
                f,
                ensure_ascii=False,
                indent=2
            )

    def __parse_article(self, url_article: str):
        text = requests.get(url_article, headers=self.headers, timeout=15)
        soup = BeautifulSoup(text.content, 'html.parser')
        text_article = soup.find('blockquote').text.strip()
        return text_article

    def __download__and_save_pdf_article(self, meta: list, sleep_sec=3):
        for value in meta:
            response = requests.get(value["pdf_url"])
            response.raise_for_status()

            reader = PdfReader(BytesIO(response.content))
            text = ""
            for page in reader.pages[:3]: # Ограничение по количеству страниц, можно проставить тут
                text += page.extract_text()

            with open(
                    os.path.join(self.name_doc_dir, f"{value['data_sub']}_{value['title']}.md"),
                    "w",
                    encoding="utf-8"
            ) as f:
                f.write(text)
            time.sleep(sleep_sec)

    def __clear_title_article(self, title: str):
        safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title)
        safe_title = re.sub(r'[_\s]+', '_', safe_title).strip('_')
        return str(safe_title)

    # def __make_md(self, md_files: set[str], meta: list[dict]):
    #     os.makedirs(self.name_doc_dir, exist_ok=True)
    #
    #     for article in self.article_list:
    #         title = article["title"]
    #         text_article = article["text_article"]
    #
    #         safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title)
    #         safe_title = re.sub(r'[_\s]+', '_', safe_title).strip('_')
    #         file_name = f"{safe_title}.md"
    #
    #         # # ⛔ уже есть — пропускаем
    #         # if file_name in md_files:
    #         #     continue
    #
    #         article["file_name"] = file_name
    #
    #         with open(
    #                 os.path.join(self.name_doc_dir, file_name),
    #                 "w",
    #                 encoding="utf-8"
    #         ) as f:
    #             f.write(text_article)


    # def __make_json(self, metadata: list[dict]):
    #     os.makedirs(self.name_meta_dir, exist_ok=True)
    #     path = os.path.join(self.name_meta_dir, "metadata.json")
    #
    #     for values in metadata:
    #         meta = values["title"].copy()
    #         metadata[meta["file_name"]] = meta
    #
    #     with open(path, "w", encoding="utf-8") as f:
    #         json.dump(
    #             list(metadata.values()),
    #             f,
    #             ensure_ascii=False,
    #             indent=2
    #         )

    # def __read_existing_md_files(self) -> set[str]:
    #     if not os.path.exists(self.name_doc_dir):
    #         return set()
    #
    #     return {
    #         name for name in os.listdir(self.name_doc_dir)
    #         if name.endswith(".md")
    #     }

    def __read_existing_metadata(self) -> dict[str, dict]:
        path = self.name_meta_dir / "metadata.json"
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {item["external_id"]: item for item in data}
        except (json.JSONDecodeError, KeyError):
            return {}
    
    def __gen_digest_for_file(self, new_articles):
        path = self.name_meta_dir / "metadata.json"
        md_files_orig = list(self.name_doc_dir.glob("*.md"))
        
        md_files_dig = list(self.name_doc_dig.rglob("*.md"))

        names = []
        for i in md_files_dig:
            names.append(i.name)

        files_to_digest = [art for art in md_files_orig if art.name not in names]

        results = []

        for filepath in files_to_digest:
            digest_result = self.summarizer.generate_summary(
                filepath.read_text(encoding='utf-8'),
                filepath.name, path
            )
            results.append({
                            "file": filepath.name,
                            "status": digest_result["status"],
                            "digest_file": digest_result.get('digest_file')
                        })
            
        return results

    #
    # async def sync_from_obs(self):
    #     await obs.download_all_md()
    #     await obs.download_metadata()

    async def run(self):
        # try:
        #     print("Считываем файлы из OBS")
        #     await self.sync_from_obs()
        #     print("Файлы на локальной машине")
        # except Exception as e:
        #     print("Статей в объектном хранилище нет!")
        meta = self.parsing()
        existing_ids = set(self.__read_existing_metadata().keys())

        # тут достать arxiv_id из ссылки
        new_articles = [art for art in meta if art["external_id"] not in existing_ids]
        self.__download__and_save_pdf_article(new_articles)
        self.__save_meta_in_json(new_articles)
        # self.__gen_digest_for_file(new_articles)


        # print("Сохранение статей")
        # try:
        #     # self.__download__and_save_pdf_article(meta)
        #     # self.__save_meta_in_json(meta)
        #     self.__make_md(md_files, meta)
        # except Exception as e:
        #     print("⛔ Проблемы с Интернетом, отвалился парсер", e)
        #
        # print("Подгрузка FAISS")
        # await obs.upload_vectorstore()
        #
        # print("Подгрузка дайджестов")
        # await  obs.upload_digests()
        #
        # await obs.upload_all_md()
        # await obs.put_json()

#
#
# parser = ARXIVParser()
# asyncio.run(parser.run())


