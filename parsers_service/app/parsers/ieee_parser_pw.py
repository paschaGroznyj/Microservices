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
from mistralai import Mistral
from datetime import datetime
import shutil
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.support import expected_conditions as EC
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
            page.wait_for_selector('.pagination-bar')
            pagination_bar = page.locator(".pagination-bar")
            pagination_links = pagination_bar.locator('button').all()[:-1]

            articles = []

            for i in range(len(pagination_links)):
                start = datetime.now()
                if i != 0:
                    time.sleep(5)
                    pagination_bar = page.locator(".pagination-bar")
                    pagination_bar.locator('button').all()[i].click()
                
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
                            text_article = self.__get_text_pdf_file(page=page, link=direct_link)
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
                                    None
                                )
                            )
                print(f'Page {i+1} processed in {datetime.now() - start}')
                page.goto(self.requested_link)

        return [asdict(item) for item in articles]
            
    def __url_to_hash(self, url: str, length: int = 12) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:length]
    
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
        
    def save_to_html(self, page):
        """
        for debug purposes only
        """
        with open('page.html', 'w', encoding='utf8') as f:
            f.write(page.content())

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
            1 / element.locator('.icon-access-open-access').count()
            1 / element.locator('.fw-bold').count()
            # TODO: logging of which element is not found
        except ZeroDivisionError:
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

        # Явный путь к системному chromium (для Docker / ARM)
        chromium_bin = shutil.which("chromium") or shutil.which("chromium-browser")
        if chromium_bin:
            options.binary_location = chromium_bin

        chromedriver_bin = shutil.which("chromedriver") or shutil.which("chromium-driver")
        service = Service(executable_path=chromedriver_bin) if chromedriver_bin else Service()

        # Create driver
        driver = webdriver.Chrome(options=options, service=service)
        
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
        return await asyncio.to_thread(self._scrape)



# async def main():
#     data_range = ('2026-01-20', '2026-01-30') # Тут фильтрация по дате пока что не предусмотрена
#     link = 'https://ieeexplore.ieee.org/search/searchresult.jsp?queryText=Process%20Mining&highlight=true&returnType=SEARCH&matchPubs=true&refinementName=Publication%20Topics&pageNumber=1&refinements=PublicationTopics:Process%20Mining&ranges=2025_2025_Year&returnFacets=ALL'
#     ieeep_parser = IEEEParser(link)
#     results = await asyncio.gather(
#         ieeep_parser.arun(),
#         return_exceptions=True
#     )
#     with open('ieee.json', 'w+', encoding='utf8') as jsonfile:
#         json.dump(results, jsonfile, indent=4, ensure_ascii=False)
#
if __name__ == "__main__":
    load_dotenv()
    link = 'https://ieeexplore.ieee.org/search/searchresult.jsp?queryText=Process%20Mining&highlight=true&returnType=SEARCH&matchPubs=true&refinementName=Publication%20Topics&pageNumber=1&refinements=PublicationTopics:Process%20Mining&ranges=2025_2025_Year&returnFacets=ALL'
    result = IEEEParser(link, ('2025-10-01', '2026-02-01'))._scrape()

    print(f'Found results: {len(result)}')

    with open('test.json', 'w+', encoding='utf8') as jsonfile:
        json.dump(result, jsonfile, ensure_ascii=False, indent=4)