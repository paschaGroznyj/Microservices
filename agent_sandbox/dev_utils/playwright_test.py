import asyncio
from playwright.async_api import async_playwright

async def fetch():
    async with async_playwright() as p:
        print("✅ Playwright запустился")

        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--no-zygote",
            ]
        )
        print("✅ Chromium запущен")

        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
        print("✅ Страница создана")

        await page.goto("https://www.banki.ru/news/lenta/", timeout=30000)
        print("✅ Страница загружена")

        await page.wait_for_load_state("networkidle", timeout=15000)
        print("✅ networkidle")

        title = await page.title()
        print(f"📄 Title: {title}")

        # Ищем data-test элементы
        data_tests = await page.evaluate("""
            () => [...document.querySelectorAll('[data-test]')]
                .map(el => ({
                    tag: el.tagName,
                    dataTest: el.getAttribute('data-test'),
                    text: el.innerText?.slice(0, 80)
                }))
        """)
        print(f"\n🔍 data-test элементов: {len(data_tests)}")
        for el in data_tests[:30]:
            print(f"  {el['tag']} | {el['dataTest']} | {el.get('text', '')[:60]}")

        # Ищем ссылки на новости
        news_links = await page.evaluate("""
            () => [...document.querySelectorAll('a[href*="/news/lenta/news/"]')]
                .map(el => ({
                    href: el.getAttribute('href'),
                    text: el.innerText?.slice(0, 100)
                }))
        """)
        print(f"\n📰 Ссылок на новости: {len(news_links)}")
        for item in news_links[:10]:
            print(f"  {item['text']} → {item['href']}")

        await browser.close()
        print("\n✅ Готово!")

asyncio.run(fetch())
