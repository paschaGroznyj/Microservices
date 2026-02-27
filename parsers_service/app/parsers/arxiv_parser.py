import re
import time
import json
import urllib
import asyncio
import hashlib
from io import BytesIO
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional, List

import requests
from bs4 import BeautifulSoup
from PyPDF2 import PdfReader
from dotenv import load_dotenv



load_dotenv()

@dataclass
class Article:
    source: str
    external_id: str
    title: str
    date_sub: str
    url_article: str
    pdf_url: str
    authors: dict
    tags: str | List
    abstract: str
    file_article: str
    category: str | None
    text_article: str


# obs = OBSWorker()
class ARXIVParser:
    name = "arxiv"

    PM_KEYWORDS = [
        '"process mining"',
        '"process discovery"',
        '"event logs"',
        '"process model"',
        '"audit trail"',
        '"conformance checking"',
        '"process enhancement"',
        '"root cause analysis"',
        '"process monitoring"',
        '"compliance checking"',
        '"deviation analysis"',
        '"continuous auditing"',
        '"business process analysis"',
        '"process intelligence"',
        '"task mining"',
        '"operational analytics"',
        '"financial auditing"',
        '"internal audit"',
        '"process compliance"',
        '"audit log analysis"'
    ]

    def __init__(self, data_range: tuple, search_word=None):
        self.months_dict = {
        'January': 1, 'February': 2, 'March': 3, 'April': 4,
            'May': 5, 'June': 6, 'July': 7, 'August': 8,
            'September': 9, 'October': 10, 'November': 11, 'December': 12
        }
        full_query_str = " OR ".join(self.PM_KEYWORDS)
        encoded_query = urllib.parse.quote(full_query_str, safe=' OR')
        self.base_url = "https://arxiv.org/search/?"
        self.parameters = {"query": encoded_query,
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
        # self.llm = GigaChat(
        #     credentials=GIGACHAT_CREDENTIALS,  # Обязательный параметр
        #     verify_ssl_certs=False,  # Отключаем проверку SSL
        # )
        # self.summarizer = SummaryGenerator()
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
        for i in range(0, 1000, 50):
            get_url = self.cat_url + str(i)
            print(f"Номер страницы: {i}")
            text = requests.get(get_url, headers=self.headers, timeout=15)
            soup = BeautifulSoup(text.content, 'html.parser')

            page_has_relevant = False

            preview_articles = [i for i in soup.find_all('li', class_="arxiv-result")]
            for art in preview_articles:
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
                file_article = f"{date_sub}_{title}.md"

                p_tag = art.find('p', class_="list-title is-inline-block")
                arxiv_id = p_tag.find('a').text.split(":")[1] # достать
                pdf_link_tag = p_tag.find('a', string='pdf')
                pdf_url = ''
                category = []
                text_article = ''

                if pdf_link_tag:
                    pdf_url = pdf_link_tag['href'].strip()
                    text_article = self.__get_text_pdf_file(pdf_url)

                result_list.append(
                    Article(
                        source="https://arxiv.org",
                        external_id=arxiv_id,
                        title=title,
                        date_sub=date_sub,
                        url_article=url_article,
                        pdf_url=pdf_url,
                        authors=authors_dict,
                        tags=tags,
                        abstract=abstract,
                        file_article=f"{date_sub}_{self.__url_to_hash(url_article)}.md",
                        category=category if category else None,
                        text_article=text_article
                    )
                )
                print("Название статьи", title)
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


        return [asdict(item) for item in result_list]

    def __url_to_hash(self, url: str, length: int = 12) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:length]

    def __parse_article(self, url_article: str):
        text = requests.get(url_article, headers=self.headers, timeout=15)
        soup = BeautifulSoup(text.content, 'html.parser')
        text_article = soup.find('blockquote').text.strip()
        return text_article

    def __get_text_pdf_file(self, link: str, sleep_sec=3):
        response = requests.get(link, headers=self.headers, timeout=20)
        response.raise_for_status()
        try:
            reader = PdfReader(BytesIO(response.content))
            text = ""
            for page in reader.pages[:4]: # Ограничение по количеству страниц, можно проставить тут
                text += page.extract_text()
            time.sleep(sleep_sec)
        except Exception as e:
            return ""
        return text

    def __clear_title_article(self, title: str):
        safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title)
        safe_title = re.sub(r'[_\s]+', '_', safe_title).strip('_')
        return str(safe_title)

    async def arun(self) -> list[dict]:
        return await asyncio.to_thread(self.parsing)

# async def main():
#     data_range = ('2026-01-20', '2026-01-30')
#     arx_parser = ARXIVParser(data_range)
#     results = await asyncio.gather(
#         arx_parser.arun(),
#         return_exceptions=True
#     )
#     with open('../arx.json', 'w+', encoding='utf8') as jsonfile:
#         json.dump(results, jsonfile, indent=4, ensure_ascii=False)
#
# if __name__ == "__main__":
#     asyncio.run(main())

