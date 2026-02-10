import re
import os
import time
import json
import yaml
import asyncio
import requests
from io import BytesIO
from PyPDF2 import PdfReader
from dotenv import load_dotenv
from dataclasses import dataclass, asdict
from mistralai import Mistral
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
    text_article: str

class IEEEParser:
    name = "ieee"

    def __init__(self, requested_link):
        self.driver = self.create_stealth_headless_driver()
        self.driver.get(requested_link)
        self.requested_link = requested_link

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
            model = os.getenv('MISTRAL_MODEL'),
            messages = [
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
        wait = WebDriverWait(self.driver, timeout=60)
        wait.until(
            EC.presence_of_all_elements_located((By.CLASS_NAME, 'pagination-bar'))
        )
        
        # Get pages
        pagination_bar = self.driver.find_element(By.CLASS_NAME, 'pagination-bar')
        pagination_links = pagination_bar.find_elements(By.TAG_NAME, 'button')[:-1]

        articles = []
        
        # Process pages
        for i in range(len(pagination_links))[:2]:
            start = datetime.now()
            if i != 0:
                time.sleep(5)
                pagination_bar = self.driver.find_element(By.CLASS_NAME, 'pagination-bar')
                pagination_bar.find_elements(By.TAG_NAME, 'button')[i].click()
            
            print(f'Processing page {i+1}')
            
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
                
                # Detemine the date of publication
                try:
                    date_string = ' '.join(DATE_PATTERN.findall(self.driver.page_source)[0])
                    date = datetime.strptime(date_string, '%d %B %Y').date()
                except IndexError:
                    print('Publication date not found')
                if date < datetime.strptime('01.10.2025', '%d.%m.%Y').date():
                    continue

                # Check if article is about PM
                title = self.driver.find_element(By.CLASS_NAME, 'document-title').text
                abstract = [x.text for x in self.driver.find_elements(By.CLASS_NAME, 'u-mb-1') if 'Abstract' in x.text][0]
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
                        text_article = self.__get_text_pdf_file(link=direct_link)
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
                                'file',
                                text_article
                            )
                        )
            print(f'Page {i+1} processed in {datetime.now() - start}')
            self.driver.get(self.requested_link)

        return [asdict(item) for item in articles]
        
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

    def __get_text_pdf_file(self, link: str, sleep_sec=3):
        cookies = {c['name']: c['value'] for c in self.driver.get_cookies()}

        headers = {
            "User-Agent": self.driver.execute_script("return navigator.userAgent;"),
            "Referer": self.driver.current_url,
            "Accept": "application/pdf"
        }

        response = requests.get(
            link,
            headers=headers,
            cookies=cookies,
            timeout=30
        )

        response.raise_for_status()

        reader = PdfReader(BytesIO(response.content))
        text = ""

        for page in reader.pages[:3]:
            extracted = page.extract_text()
            if extracted:
                text += extracted + "\n"

        time.sleep(sleep_sec)
        return text

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
        
        # Use new headless mode
        options.add_argument('--headless=new')
        
        # Disable automation flags
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        
        # Normal browser settings
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--start-maximized')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--disable-web-security')
        options.add_argument('--allow-running-insecure-content')
        
        # Realistic user agent
        options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36')
        
        # Enable features
        options.add_argument('--enable-webgl')
        options.add_argument('--enable-3d-apis')
        options.add_argument('--enable-javascript')

        options.add_argument('--enable-features=NetworkService,NetworkServiceInProcess')
        options.add_argument('--disable-features=VizDisplayCompositor')
        
        # Disable logging
        options.add_argument('--log-level=3')
        options.add_argument('--silent')

        options.add_argument('--disable-pdf-viewer')
        options.add_argument('--disable-features=ChromePDFViewer')
        
        
        # Set preferences
        prefs = {
            "download.prompt_for_download": False,  # Disable download prompt
            "download.directory_upgrade": True,
            "profile.default_content_setting_values.notifications": 2,
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False,
            "webrtc.ip_handling_policy": "disable_non_proxied_udp",
            "webrtc.multiple_routes_enabled": False,
            "webrtc.nonproxied_udp_enabled": False,
            "plugins.plugins_disabled": ["Chrome PDF Viewer"], # Disable Chrome's PDF Viewer , 
            "download.extensions_to_open": "application/pdf",
            "plugins.always_open_pdf_externally": True, 
        }
        options.add_experimental_option("prefs", prefs)
        
        # Create driver
        driver = webdriver.Chrome(options=options)
        
        # Execute stealth scripts
        stealth_scripts = [
            # Hide automation
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            """,
            
            # Override permissions
            """
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ? 
                    Promise.resolve({ state: Notification.permission }) : 
                    originalQuery(parameters)
            );
            """,
            
            # Spoof hardware concurrency
            """
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => 8
            });
            """,
        ]
        
        for script in stealth_scripts:
            driver.execute_script(script)
        
        return driver

    async def arun(self) -> list[dict]:
        return await asyncio.to_thread(self.parse)


async def main():
    data_range = ('2026-01-20', '2026-01-30') # Тут фильтрация по дате пока что не предусмотрена
    link = 'https://ieeexplore.ieee.org/search/searchresult.jsp?queryText=Process%20Mining&highlight=true&returnType=SEARCH&matchPubs=true&refinementName=Publication%20Topics&pageNumber=1&refinements=PublicationTopics:Process%20Mining&ranges=2025_2025_Year&returnFacets=ALL'
    ieeep_parser = IEEEParser(link)
    results = await asyncio.gather(
        ieeep_parser.arun(),
        return_exceptions=True
    )
    with open('ieee.json', 'w+', encoding='utf8') as jsonfile:
        json.dump(results, jsonfile, indent=4, ensure_ascii=False)

if __name__ == "__main__":
    asyncio.run(main())
