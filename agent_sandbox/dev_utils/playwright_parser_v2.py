"""
Универсальный Playwright-парсер v2
Фиксы:
  - banki.ru: networkidle → domcontentloaded + wait_for_selector
  - dns-shop: антибот-обход (extra_http_headers, viewport, js-stealth)
  - output: пишем в /tmp/output/ (read-only filesystem fix)
  - tass.ru: JS-challenge (ServicePipe) — ждём прохождения challenge

Запуск:
    python playwright_parser_v2.py banki
    python playwright_parser_v2.py tass
    python playwright_parser_v2.py tass --limit 50
    python playwright_parser_v2.py dns
    python playwright_parser_v2.py dns --limit 50 --pages 2
"""

import asyncio
import os
import sys
import json
import csv
import argparse
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

# ─── Конфиг браузера ────────────────────────────────────────────────────────

BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--no-zygote",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-blink-features=AutomationControlled",  # скрываем webdriver
]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# JS для скрытия признаков headless
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en-US']});
window.chrome = {runtime: {}};
"""

# ─── Парсер banki.ru/news/lenta ─────────────────────────────────────────────

async def parse_banki(page, limit=30):
    url = "https://www.banki.ru/news/lenta/"
    print(f"[banki] Загружаем {url}")

    # FIX: domcontentloaded вместо networkidle
    await page.goto(url, timeout=45000, wait_until="domcontentloaded")

    # Ждём появления ленты новостей
    print("[banki] Ждём селектор news-lenta-list-view...")
    try:
        await page.wait_for_selector('[data-test="news-lenta-list-view"]', timeout=20000)
        print("[banki] ✅ Лента найдена")
    except Exception:
        print("[banki] ⚠️  Таймаут селектора, пробуем всё равно")

    print(f"[banki] Title: {await page.title()}")

    # Скролл для подгрузки
    for i in range(4):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.2)
        print(f"[banki] Скролл {i+1}/4")

    results = []

    # Стратегия 1 — через контейнер ленты
    lenta = await page.query_selector('[data-test="news-lenta-list-view"]')
    if lenta:
        links = await lenta.query_selector_all("a[href*='/news/']")
        print(f"[banki] Стратегия 1: найдено ссылок = {len(links)}")
        for link in links:
            href = await link.get_attribute("href")
            text = (await link.inner_text()).strip()
            if href and text and len(text) > 10:
                full_url = f"https://www.banki.ru{href}" if href.startswith("/") else href
                results.append({"title": text, "url": full_url})

    # Стратегия 2 — все /news/lenta/ ссылки на странице
    if not results:
        print("[banki] Стратегия 2: ищем a[href*='/news/lenta/']")
        links = await page.query_selector_all("a[href*='/news/']")
        for link in links:
            href = await link.get_attribute("href")
            text = (await link.inner_text()).strip()
            if href and text and len(text) > 15 and "/news/lenta/" not in href:
                full_url = f"https://www.banki.ru{href}" if href.startswith("/") else href
                results.append({"title": text, "url": full_url})

    # Дедупликация
    seen = set()
    unique = []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)

    return unique[:limit]


# ─── Парсер tass.ru/sport ────────────────────────────────────────────────────

async def parse_tass(page, url="https://tass.ru/sport", limit=30):
    print(f"[tass] Загружаем {url}")

    # Stealth — скрываем headless ДО загрузки страницы
    await page.add_init_script(STEALTH_JS)

    try:
        await page.goto(url, timeout=60000, wait_until="domcontentloaded")
    except Exception as e:
        print(f"[tass] ⚠️  goto: {e}")

    # ServicePipe JS-challenge: ждём пока spinner исчезнет и появится реальный контент
    # Challenge обычно проходит за 3-6 секунд
    print("[tass] Ждём прохождения JS-challenge (ServicePipe)...")

    challenge_passed = False
    for attempt in range(12):  # до 24 секунд
        await asyncio.sleep(2)
        title = await page.title()
        html_len = await page.evaluate("document.body.innerHTML.length")
        spinner = await page.query_selector("#id_spinner")
        spinner_visible = False
        if spinner:
            spinner_visible = await spinner.is_visible()

        print(f"[tass]   attempt {attempt+1}: title='{title}' | html_len={html_len} | spinner={spinner_visible}")

        # Challenge пройден — spinner скрыт и HTML заметно вырос
        if not spinner_visible and html_len > 10000:
            challenge_passed = True
            print("[tass] ✅ JS-challenge пройден!")
            break

    if not challenge_passed:
        html_snippet = await page.evaluate("document.body.innerHTML.slice(0, 1000)")
        print(f"[tass] ❌ Challenge не пройден. HTML:\n{html_snippet}")
        return []

    print(f"[tass] Title: {await page.title()}")

    # Скролл для подгрузки новостей
    for i in range(4):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.5)
        print(f"[tass] Скролл {i+1}/4")

    results = []

    # ТАСС: карточки новостей — несколько стратегий
    # Стратегия 1: data-testid или class содержащий "news-list"
    selectors_to_try = [
        "a[href*='/sport/']",
        "[class*='news-item'] a",
        "[class*='card'] a[href*='/sport/']",
        "article a[href*='/sport/']",
        "[class*='tape'] a",
    ]

    for sel in selectors_to_try:
        links = await page.query_selector_all(sel)
        if links:
            print(f"[tass] Селектор '{sel}' → {len(links)} ссылок")
            for link in links:
                href = await link.get_attribute("href")
                text = (await link.inner_text()).strip()
                # Фильтруем: только ссылки на конкретные статьи (содержат цифры)
                if href and text and len(text) > 15 and any(c.isdigit() for c in href):
                    full_url = f"https://tass.ru{href}" if href.startswith("/") else href
                    results.append({"title": text[:200], "url": full_url})
            if results:
                break

    # Стратегия 2 — все ссылки на домене tass.ru с /sport/
    if not results:
        print("[tass] Стратегия 2: все a[href*='/sport/']")
        links = await page.query_selector_all("a")
        for link in links:
            href = await link.get_attribute("href") or ""
            text = (await link.inner_text()).strip()
            if "/sport/" in href and len(text) > 15 and any(c.isdigit() for c in href):
                full_url = f"https://tass.ru{href}" if href.startswith("/") else href
                if "tass.ru" in full_url:
                    results.append({"title": text[:200], "url": full_url})

    # Дедупликация
    seen = set()
    unique = []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)

    return unique[:limit]


# ─── Парсер dns-shop.ru ──────────────────────────────────────────────────────

async def parse_dns(page, url, limit=30, max_pages=1):
    print(f"[dns] Загружаем {url}")

    # Stealth: скрываем признаки headless
    await page.add_init_script(STEALTH_JS)

    results = []
    current_url = url
    page_num = 1

    while page_num <= max_pages:
        print(f"[dns] Страница {page_num}...")

        # FIX: domcontentloaded + большой таймаут
        try:
            await page.goto(current_url, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"[dns] ⚠️  goto timeout: {e}")

        # Пауза — даём JS время отработать
        await asyncio.sleep(3)

        title = await page.title()
        print(f"[dns] Title: '{title}'")

        # Проверяем на блокировку
        if "captcha" in title.lower() or "robot" in title.lower():
            print("[dns] ❌ Обнаружена капча/блокировка")
            break

        # Ждём товары
        selectors_to_try = [
            ".catalog-product",
            "[data-id]",
            "article.product",
            ".product-item",
            "[class*='catalog-product']",
        ]
        items = []
        for sel in selectors_to_try:
            try:
                await page.wait_for_selector(sel, timeout=8000)
                items = await page.query_selector_all(sel)
                if items:
                    print(f"[dns] Селектор сработал: '{sel}' → {len(items)} элементов")
                    break
            except Exception:
                continue

        if not items:
            # Дамп HTML для диагностики
            html_snippet = await page.evaluate("document.body.innerHTML.slice(0, 2000)")
            print(f"[dns] ⚠️  Товары не найдены. HTML snippet:\n{html_snippet}\n")
            break

        # Скролл для ленивой загрузки
        for _ in range(5):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1.0)

        # Повторно берём товары после скролла
        items = await page.query_selector_all(selectors_to_try[0])
        print(f"[dns] После скролла: {len(items)} товаров")

        for item in items:
            try:
                name_el = await item.query_selector(
                    ".catalog-product__name, [data-role='name'], a.catalog-product__name, "
                    ".product-item__name, [class*='product-name']"
                )
                name = (await name_el.inner_text()).strip() if name_el else ""

                link_el = await item.query_selector("a[href*='/product/']")
                href = await link_el.get_attribute("href") if link_el else ""
                full_url = f"https://www.dns-shop.ru{href}" if href and href.startswith("/") else href

                price_el = await item.query_selector(
                    ".product-buy__price, .catalog-product__price-current, "
                    "[class*='price-current'], .price__current, [class*='price']"
                )
                price = (await price_el.inner_text()).strip() if price_el else ""
                price = price.replace("\xa0", " ").replace("\u20bd", "₽")

                data_id = await item.get_attribute("data-id") or ""

                if name or full_url:
                    results.append({
                        "id": data_id,
                        "name": name,
                        "price": price,
                        "url": full_url,
                        "page": page_num,
                    })
            except Exception as e:
                print(f"[dns] ⚠️  Ошибка карточки: {e}")
                continue

        if len(results) >= limit:
            break

        # Пагинация
        next_btn = await page.query_selector(
            "a.pagination-widget__page-link[rel='next'], .pagination__next, "
            "a[class*='pagination'][rel='next']"
        )
        if next_btn and page_num < max_pages:
            next_href = await next_btn.get_attribute("href")
            if next_href:
                current_url = f"https://www.dns-shop.ru{next_href}" if next_href.startswith("/") else next_href
                page_num += 1
            else:
                break
        else:
            break

    return results[:limit]


# ─── Сохранение ──────────────────────────────────────────────────────────────

def save_results(data, name, fmt="both"):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # FIX: /tmp/output/ — всегда writable
    out_dir = Path("/tmp/output")
    out_dir.mkdir(parents=True, exist_ok=True)

    if fmt in ("json", "both"):
        path = out_dir / f"{name}_{timestamp}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"💾 JSON: {path}")

    if fmt in ("csv", "both") and data:
        path = out_dir / f"{name}_{timestamp}.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
        print(f"💾 CSV:  {path}")


# ─── Main ────────────────────────────────────────────────────────────────────

async def main():
    ap = argparse.ArgumentParser(description="Playwright Parser v2")
    ap.add_argument("target", choices=["banki", "dns", "tass"])
    ap.add_argument("--url",   default="")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--fmt",   default="both", choices=["json", "csv", "both"])
    args = ap.parse_args()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        context = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            extra_http_headers={
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
                "sec-ch-ua-platform": '"Windows"',
            }
        )
        page = await context.new_page()

        # Блокируем только аналитику (не картинки — DNS их использует)
        await page.route("**/{gtm,analytics,pixel,facebook,counter}**", lambda r: r.abort())

        try:
            if args.target == "banki":
                print("=" * 50)
                print("🏦  banki.ru/news/lenta")
                print("=" * 50)
                data = await parse_banki(page, limit=args.limit)
                print(f"\n📰 Итого новостей: {len(data)}")
                for i, item in enumerate(data[:10], 1):
                    print(f"  {i:02d}. {item['title'][:70]}")
                    print(f"      {item['url']}")
                save_results(data, "banki_news", args.fmt)

            elif args.target == "tass":
                url = args.url or "https://tass.ru/sport"
                print("=" * 50)
                print(f"📰  ТАСС Спорт")
                print(f"    URL: {url}")
                print("=" * 50)
                data = await parse_tass(page, url=url, limit=args.limit)
                print(f"\n📰 Итого новостей: {len(data)}")
                for i, item in enumerate(data[:10], 1):
                    print(f"  {i:02d}. {item['title'][:70]}")
                    print(f"      {item['url']}")
                save_results(data, "tass_sport", args.fmt)

            elif args.target == "dns":
                url = args.url or "https://www.dns-shop.ru/catalog/personal/?category=17a8a01d16404e77"
                print("=" * 50)
                print(f"🖥   DNS-shop")
                print(f"    URL: {url}")
                print("=" * 50)
                data = await parse_dns(page, url, limit=args.limit, max_pages=args.pages)
                print(f"\n🛒 Итого товаров: {len(data)}")
                for i, item in enumerate(data[:10], 1):
                    print(f"  {i:02d}. {item['name'][:60]}")
                    print(f"      Цена: {item['price']} | {item['url']}")
                save_results(data, "dns_catalog", args.fmt)

        finally:
            await browser.close()
            print("\n✅ Готово!")

if __name__ == "__main__":
    asyncio.run(main())
