import re
import os
import time
import json
import hashlib
import asyncio
import requests
from io import BytesIO
from PyPDF2 import PdfReader
from dotenv import load_dotenv
from typing import Optional
from dataclasses import dataclass, asdict
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


DATE_PATTERN = re.compile(
    r'\b(\d{1,2})\s+([Jj]anuary|[Ff]ebruary|[Mm]arch|[Aa]pril|[Mm]ay|[Jj]une|[Jj]uly|[Aa]ugust|[Ss]eptember|[Oo]ctober|[Nn]ovember|[Dd]ecember)\s+(\d{4})\b'
)

@dataclass
class Article:
    source: str
    external_id: str
    title: str
    date_sub: str
    url_article: str
    pdf_url: str
    authors: dict
    tags: str
    abstract: str
    file_article: str
    text_article: str
    category: str | None
    pdf_bytes: bytes = b""

class IEEEParser:
    name = "ieee"

    def __init__(self, requested_link, date_range: Optional[tuple[str, str]] = None):
        self.requested_link = requested_link
        self.date_range = date_range

        print('PARSER INITIALIZED')

    def _in_date_range(self, date_str: str) -> bool:
        if not self.date_range:
            return True

        try:
            article_date = datetime.fromisoformat(date_str)
            start = datetime.fromisoformat(self.date_range[0])
            end = datetime.fromisoformat(self.date_range[1])
            return start <= article_date <= end
        except Exception:
            return False
    
    @staticmethod
    def find_link(element):
        try:
            return element.locator('.fw-bold').get_attribute('href')
        except PlaywrightTimeoutError:
            return False
    
    def _scrape(self):
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir='.',
                headless=True,
                locale="ru-RU",
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36'
            )
            page = ctx.new_page()

            page.set_default_timeout(30_000)
            page.set_default_navigation_timeout(30_000)

            page.goto(self.requested_link)
            # page.wait_for_selector('.pagination-bar')
            # pagination_bar = page.locator(".pagination-bar")
            # pagination_links = pagination_bar.locator('button').all()[:-1]

            articles = []

            for i in range(4):
                start = datetime.now()
                if i != 0:
                    print(f"\n--- ПЕРЕХОД НА СТРАНИЦУ {i + 1} ---")
                    time.sleep(5)

                    # 1. Проверяем, видна ли панель вообще
                    pagination_bar = page.locator(".pagination-bar")
                    if not pagination_bar.is_visible():
                        print("ОШИБКА: Pagination bar не видна!")
                        # Попробуем сделать скриншот, чтобы увидеть, что не так
                        page.screenshot(path=f"debug_page_{i}_no_pagination.png")
                        continue

                        # 2. Получаем все кнопки
                    buttons = pagination_bar.locator('button').all()
                    print(f"Найдено кнопок пагинации: {len(buttons)}")

                    # Выведем текст/атрибуты кнопок, чтобы понять, та ли это кнопка
                    for idx, btn in enumerate(buttons):
                        try:
                            txt = btn.text_content()
                            aria = btn.get_attribute('aria-label')
                            print(f"  Кнопка {idx}: Text='{txt}', Aria='{aria}'")
                        except:
                            print(f"  Кнопка {idx}: <недоступна>")

                    # 3. Проверка границ
                    if i >= len(buttons):
                        print(f"ОШИБКА: Индекс {i} выходит за пределы списка кнопок ({len(buttons)})")
                        break

                    target_btn = buttons[i]

                    # 4. Проверяем, кликабельна ли кнопка
                    if not target_btn.is_enabled():
                        print(f"ОШИБКА: Кнопка страницы {i + 1} не активна (disabled)")
                        break

                    print(f"Кликаем по кнопке индекс {i}...")

                    # 5. Делаем клик с ожиданием загрузки сети или изменения URL
                    # Важно: click может не ждать загрузки новой страницы, если это SPA
                    target_btn.click()

                    # Ждем, пока URL изменится или появится индикатор загрузки
                    # IEEE Explore часто меняет URL при пагинации, например ?pageNumber=2
                    try:
                        page.wait_for_url(lambda url: f"pageNumber={i + 1}" in url or f"page={i + 1}" in url,
                                          timeout=10000)
                        print(f"URL изменился успешно: {page.url}")
                    except:
                        print("URL не изменился ожидаемым образом, возможно, это AJAX запрос.")
                        # Альтернатива: ждем исчезновения лоадера или появления новых результатов
                        page.wait_for_load_state("networkidle")
                        print("Состояние networkidle достигнуто.")

                    # Обновляем ссылку на бар, так как DOM мог обновиться
                    pagination_bar = page.locator(".pagination-bar")
                
                print(f'Processing page #{i+1}')

                page.wait_for_selector('.List-results-items')

                search_results = page.locator(".List-results-items").all()
                suitable_results = [sr for sr in search_results if self.is_suitable_search_result(sr)]
                links = [self.find_link(sr) for sr in suitable_results]
                print(links)

                print(f'Search results: {len(search_results)}, suitable results: {len(suitable_results)}, links: {len(links)}')
                
                # Process search results
                for link in links:
                    if not link:
                        continue

                    page.goto('https://ieeexplore.ieee.org' + link)

                    try:
                        date_string = ' '.join(DATE_PATTERN.findall(page.content())[0])
                        date = datetime.strptime(date_string, '%d %B %Y').date()
                        iso_date = date.isoformat()
                        date_string = date.strftime('%Y-%m-%d')
                        print(date)
                    except IndexError:
                        print('Publication date not found')
                    
                    if not self._in_date_range(iso_date):
                        continue

                    title = page.locator('.document-title').text_content()
                    abstract = [x.text_content() for x in page.locator('.u-mb-1').all() if 'Abstract' in x.text_content()][0]

                    authors = {}
                    for author_element in page.locator('.authors-info').all():
                        link = author_element.locator('a').first.get_attribute('href')
                        name = author_element.locator('span').first.text_content()
                        authors[name] = link

                    tags = []
                    page.locator('#keywords').click()
                    tags_containers = page.locator('.doc-keywords-list-item').all()
                    for tc in tags_containers:
                        if tc.locator('strong').text_content() == 'IEEE Keywords':
                            tags = [item.text_content() for item in tc.locator('a').all()]
                    
                    # Find the PDF button
                    pdf_button = page.locator('.xpl-btn-pdf')
                    pdf_link = pdf_button.get_attribute('href')

                    # Go to PDF
                    page.goto('https://ieeexplore.ieee.org' + pdf_link)
                    
                    frames = page.locator('iframe').all()
                    for frame in frames:
                        if 'iframe src' in frame.evaluate('el => el.outerHTML'):
                            direct_link = frame.get_attribute('src')
                            text_article, pdf_bytes = self.__get_text_pdf_file(page=page, link=direct_link)
                            articles.append(
                                Article(
                                    'https://ieeexplore.ieee.org/',
                                    link.split('/')[-1],
                                    title,
                                    date_string,
                                    link,
                                    direct_link,
                                    authors,
                                    ' '.join(tags),
                                    abstract,
                                    f'{iso_date}_{self.__url_to_hash(link)}.md',
                                    text_article,
                                    None,
                                    pdf_bytes
                                )
                            )
                print(f'Page {i+1} processed in {datetime.now() - start}')
                page.goto(self.requested_link)

        return [asdict(item) for item in articles]
            
    def __url_to_hash(self, url: str, length: int = 12) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:length]


    def __get_text_pdf_file(self, page, link: str, sleep_sec=3):

        cookies = {c['name']: c['value'] for c in page.context.cookies(page.url)}

        headers = {
            "User-Agent": page.evaluate("navigator.userAgent;"),
            "Referer": page.url,
            "Accept": "application/pdf"
        }

        response = requests.get(
            link,
            headers=headers,
            cookies=cookies,
            timeout=30
        )

        response.raise_for_status()
        pdf_bytes = response.content

        reader = PdfReader(BytesIO(pdf_bytes))
        text = ""

        for page in reader.pages[:3]:
            extracted = page.extract_text()
            if extracted:
                text += extracted + "\n"

        time.sleep(sleep_sec)
        return text, pdf_bytes

    @staticmethod
    def is_suitable_search_result(element):
        try:
            1 / element.locator('.icon-access-open-access').count()
            1 / element.locator('.fw-bold').count()
            # TODO: logging of which element is not found
        except ZeroDivisionError:
            return False
        return True
    


    async def arun(self) -> list[dict]:
        return await asyncio.to_thread(self._scrape)

if __name__ == "__main__":
    load_dotenv()
    link = 'https://ieeexplore.ieee.org/search/searchresult.jsp?queryText=Process%20Mining&highlight=true&returnType=SEARCH&matchPubs=true&refinementName=Publication%20Topics&refinements=PublicationTopics:Process%20Mining&returnFacets=ALL&ranges=2026_2026_Year'
    result = IEEEParser(link, ('2026-04-10', '2026-04-17'))._scrape()

    print(f'Found results: {len(result)}')

    with open('test.json', 'w+', encoding='utf8') as jsonfile:
        json.dump(result, jsonfile, ensure_ascii=False, indent=4)