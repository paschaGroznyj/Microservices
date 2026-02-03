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

from langchain.chat_models.gigachat import GigaChat
from langchain.schema import SystemMessage
from core.config import GIGACHAT_CREDENTIALS

from .botocore_client import OBSWorker
from summary import SummaryGenerator

load_dotenv()

obs = OBSWorker()
class ARXIVParser:
    def __init__(self):
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

        return f"{year}-{month}-{day}"


    def __parsing_page_articles(self):
        for i in range(0, 50, 50):
            get_url = self.cat_url + str(i)
            text = requests.get(get_url)
            soup = BeautifulSoup(text.content, 'html.parser')

            result_list = []

            preview_articles = [i for i in soup.find_all('li', class_="arxiv-result")]
            for art in preview_articles[:5]:
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
                date_sub = self.__convert_month(date_sub)
                authors = art.find('p', class_='authors')
                authors_dict = {i.text: i.get('href') for i in authors.find_all('a')}
                abstract = self.__parse_article(url_article)


                p_tag = art.find('p', class_="list-title is-inline-block")
                arxiv_id = p_tag.find('a').text.split(":")[1]
                pdf_link_tag = p_tag.find('a', string='pdf')
                pdf_url = None

                if pdf_link_tag:
                    pdf_url = pdf_link_tag['href'].strip()
                    # try:
                    #     text_article = self.__download_pdf_article(pdf_url)
                    # except Exception as e:
                    #     print(f"Ошибка при скачивании PDF {pdf_url}: {e}")
                    #     text_article = None

                category = self.__get_category(abstract)

                result_list.append({
                       "arxiv_id": arxiv_id,
                       "title": title,
                       "data_sub": date_sub,
                       "abstract": abstract,
                       "url_article": url_article,
                       "pdf_url": pdf_url,
                       "authors": authors_dict,
                       "tags": tags,
                       "category": category
                })

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
            existing[art["arxiv_id"]] = art

        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                list(existing.values()),
                f,
                ensure_ascii=False,
                indent=2
            )

    def __parse_article(self, url_article: str):
        text = requests.get(url_article)
        soup = BeautifulSoup(text.content, 'html.parser')
        text_article = soup.find('blockquote').text.strip()
        return text_article

    def __download__and_save_pdf_article(self, meta: list):
        for value in meta:
            print(value["title"])
            response = requests.get(value["pdf_url"])
            response.raise_for_status()

            reader = PdfReader(BytesIO(response.content))
            text = ""
            for page in reader.pages[:3]:
                text += page.extract_text()

            with open(
                    os.path.join(self.name_doc_dir, f"{value['data_sub']}_{value['title']}.md"),
                    "w",
                    encoding="utf-8"
            ) as f:
                f.write(text)

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
                return {item["arxiv_id"]: item for item in data}
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


    async def sync_from_obs(self):
        await obs.download_all_md()
        await obs.download_metadata()

    async def run(self):
        try:
            print("Считываем файлы из OBS")
            await self.sync_from_obs()
            print("Файлы на локальной машине")
        except Exception as e:
            print("Статей в объектном хранилище нет!")
        print("Запуск парсера")
        try:
            meta = self.__parsing_page_articles()
            existing_ids = set(self.__read_existing_metadata().keys())
            new_articles = [art for art in meta if art["arxiv_id"] not in existing_ids]
            print("Список новых статей", new_articles)
            self.__download__and_save_pdf_article(new_articles)
            self.__save_meta_in_json(new_articles)
            self.__gen_digest_for_file(new_articles)
        except Exception as e:
            print("У нас отвалился портал", e[:100])

        # print("Сохранение статей")
        # try:
        #     # self.__download__and_save_pdf_article(meta)
        #     # self.__save_meta_in_json(meta)
        #     self.__make_md(md_files, meta)
        # except Exception as e:
        #     print("⛔ Проблемы с Интернетом, отвалился парсер", e)

        print("Подгрузка FAISS")
        await obs.upload_vectorstore()

        print("Подгрузка дайджестов")
        await  obs.upload_digests()

        await obs.upload_all_md()
        await obs.put_json()

#
#
# parser = ARXIVParser()
# asyncio.run(parser.run())


