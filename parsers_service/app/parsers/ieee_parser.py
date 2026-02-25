import re
import os
import time
import json
import yaml
import asyncio
import hashlib
from typing import List
from dotenv import load_dotenv
from dataclasses import dataclass, asdict
# from mistralai import Mistral
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.support import expected_conditions as EC

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
    category: str | None
    text_article: str


class IEEEParser:
    name = 'ieee'

    def __init__(self, requested_link):

        self.driver = self.create_stealth_headless_driver()
        self.driver.get(requested_link)
        self.requested_link = requested_link
        self._dismiss_cookie_banner()
        load_dotenv()

        print('PARSER INITIALIZED')

    @staticmethod
    def is_pm(title, abstract):
        client = Mistral(os.getenv('MISTRAL_KEY'))

        system_prompt = """
        SUPER IMPORTANT: RESPOND ONLY WITH JSON STRING!!!
        DO NOT USE ANY MARKDOWN, ONLY JSON STRING STARTING WITH { AND ENDING WITH } !!!
        You're world class scientific journal editor. Your ability do assign topics to articles is immaculate.
        You will be provided with title and abstract of an article. Determine if this article suits the topic of Proccess Mining.
        If it does, respond only with {"is_suitable": "True"}. If it does not, respond with {"is_suitable": "False"}. Do not respond with anything else.
        """

        chat_response = client.chat.complete(
            model=os.getenv('MISTRAL_MODEL'),
            messages=[
                {
                    'role': 'system',
                    'content': system_prompt
                },
                {
                    'role': 'user',
                    'content': f'Title: {title}, Abstract: {abstract}',
                },
            ]
        )

        try:
            json_response = json.loads(chat_response.choices[0].message.content)
        except json.JSONDecodeError:
            # TODO: Logging of erroneuous responses
            return {'is_suitable': 'False'}

        return json_response

    @staticmethod
    def find_link(element):
        try:
            return element.find_element(By.CLASS_NAME, 'fw-bold').get_attribute('href')
        except NoSuchElementException:
            return False

    def parse(self):
        print("Current URL:", self.driver.current_url)
        print("Page title:", self.driver.title)
        print("ТЕКСТ СТРАНИЦЫ", self.driver.page_source[:200])
        wait = WebDriverWait(self.driver, timeout=60)
        # wait.until(
        #     EC.presence_of_all_elements_located((By.CLASS_NAME, 'pagination-bar'))
        # )
        self._dismiss_cookie_banner()
        # # Get pages
        # pagination_bar = self.driver.find_element(By.CLASS_NAME, 'pagination-bar')
        # pagination_links = pagination_bar.find_elements(By.TAG_NAME, 'button')[:-1]

        articles = []

        # Process pages
        for i in range(3):
            start = datetime.now()
            if i != 0:
                time.sleep(5)
                self._dismiss_cookie_banner()
            #     pagination_bar = self.driver.find_element(By.CLASS_NAME, 'pagination-bar')
            #     pagination_bar.find_elements(By.TAG_NAME, 'button')[i].click()

            print(f'Processing page {i + 1}')

            # Get search results
            wait.until(
                EC.presence_of_all_elements_located((By.CLASS_NAME, 'List-results-items'))
            )
            search_results = self.driver.find_elements(By.CLASS_NAME, 'List-results-items')
            suitable_results = (sr for sr in search_results if self.is_suitable_search_result(sr))
            links = [self.find_link(sr) for sr in suitable_results]

            # Process search results
            for link in links:
                if not link:
                    continue
                self.driver.get(link)
                self._dismiss_cookie_banner()
                # Detemine the date of publication
                try:
                    date_string = ' '.join(DATE_PATTERN.findall(self.driver.page_source)[0])
                    date = datetime.strptime(date_string, '%d %B %Y').date()
                except IndexError:
                    print('Publication date not found')
                if date < datetime.strptime('01.10.2025', '%d.%m.%Y').date():
                    continue
                date_string = date.strftime('%Y-%m-%d')
                # Check if article is about PM
                title = self.driver.find_element(By.CLASS_NAME, 'document-title').text
                abstract = [x.text for x in self.driver.find_elements(By.CLASS_NAME, 'u-mb-1') if 'Abstract' in x.text][
                    0]
                # is_pm_response = self.is_pm(title, abstract)
                # print(is_pm_response)
                # if is_pm_response['is_suitable'] == 'False':
                #     continue

                # Get authors
                authors = {}
                for author_element in self.driver.find_elements(By.CLASS_NAME, 'authors-info'):
                    link = author_element.find_element(By.TAG_NAME, 'a').get_attribute('href')
                    name = author_element.find_element(By.TAG_NAME, 'span').text
                    authors[name] = link

                # Get tags
                tags = []
                self._dismiss_cookie_banner()
                self.driver.find_element(By.ID, 'keywords').click()
                tags_containers = self.driver.find_elements(By.CLASS_NAME, 'doc-keywords-list-item')
                for tc in tags_containers:
                    if tc.find_element(By.TAG_NAME, 'strong').text == 'IEEE Keywords':
                        tags = [item.text for item in tc.find_elements(By.TAG_NAME, 'a')]

                # Find the PDF button
                pdf_button = self.driver.find_element(By.CLASS_NAME, 'xpl-btn-pdf')
                pdf_link = pdf_button.get_attribute('href')

                # Go to PDF
                pdf_button.click()
                if 'denied' in self.driver.current_url:
                    self.driver.get(pdf_link)

                # Download file
                wait.until(EC.presence_of_all_elements_located((By.TAG_NAME, 'iframe')))
                frames = self.driver.find_elements(By.TAG_NAME, 'iframe')
                for frame in frames:
                    if 'iframe src' in frame.get_attribute('outerHTML'):
                        direct_link = frame.get_attribute('src')
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
                                f"{date_string}_{self.__url_to_hash(link)}.md",
                                None,
                                text_article=abstract
                            )
                        )
            print(f'Page {i + 1} processed in {datetime.now() - start}')
            self.driver.get(self.requested_link)

        return [asdict(item) for item in articles]

    def __url_to_hash(self, url: str, length: int = 12) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:length]

    def save_to_html(self):
        """
        for debug purposes only
        """
        with open('page.html', 'w', encoding='utf8') as f:
            f.write(self.driver.page_source)

    def download(self, link):
        self.driver.get(link)
        time.sleep(2)
        files = os.listdir(self.download_path)
        while any(f.endswith('.crdownload') for f in files):
            time.sleep(1)
            files = os.listdir(self.download_path)

    @staticmethod
    def is_suitable_search_result(element):
        try:
            element.find_element(By.CLASS_NAME, 'icon-access-open-access')
            element.find_element(By.CLASS_NAME, 'fw-bold')
            # TODO: logging of which element is not found
        except NoSuchElementException:
            return False
        return True

    def create_stealth_headless_driver(self):
        options = Options()
    
        # Headless режим
        options.add_argument('--headless=new')
        
        # Важные аргументы для контейнера
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        
        # Антидетект
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        
        # User agent
        options.add_argument('--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        # Настройки для PDF
        prefs = {
            "download.prompt_for_download": False,
            "plugins.always_open_pdf_externally": True,
            "download.open_pdf_in_system_reader": False,
            "profile.default_content_setting_values.automatic_downloads": 1,
        }
        options.add_experimental_option("prefs", prefs)
        
        # driver = webdriver.Remote(
        #     command_executor='http://selenium_chrome:4444/wd/hub',
        #     options=options
        # )
        driver = webdriver.Chrome(options=options)
        # Работа с браузером
        driver.get("https://google.com")
        print( "GOOOGLE", driver.title)


        # Stealth скрипты
        driver.execute_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        return driver

    def _dismiss_cookie_banner(self, timeout: int = 6) -> None:
        try:
            btn = WebDriverWait(self.driver, timeout).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//button[contains(., 'Accept') or contains(., 'I Agree') or contains(., 'Принять') or contains(., 'Соглас')]"
                ))
            )
            btn.click()
            time.sleep(0.3)
            return
        except Exception:
            pass

        try:
            self.driver.execute_script("""
                const el = document.querySelector('.osano-cm-window__dialog, .osano-cm-dialog, [role="dialog"].osano-cm-dialog');
                if (el) el.remove();
            """)
            time.sleep(0.2)
        except Exception:
            pass

    async def arun(self) -> list[dict]:
        return await asyncio.to_thread(self.parse)

# async def main():
#     data_range = ('2026-01-20', '2026-01-30') # Тут фильтрация по дате пока что не предусмотрена
#     link = 'https://ieeexplore.ieee.org/search/searchresult.jsp?queryText=Process%20Mining&highlight=true&returnType=SEARCH&matchPubs=true&refinementName=Publication%20Topics&pageNumber=1&refinements=PublicationTopics:Process%20Mining&ranges=2025_2026_Year&returnFacets=ALL'
#     ieeep_parser = IEEEParser(link)
#     results = await asyncio.gather(
#         ieeep_parser.arun(),
#         return_exceptions=True
#     )
#     with open('ieee.json', 'w+', encoding='utf8') as jsonfile:
#         json.dump(results, jsonfile, indent=4, ensure_ascii=False)

# if __name__ == "__main__":
#     asyncio.run(main())