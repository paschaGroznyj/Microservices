from playwright.async_api import async_playwright
from pathlib import Path

CHROMIUM_PATH = "/usr/bin/chromium"

_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--no-zygote",
    "--js-flags=--max-old-space-size=2048",
    "--force-device-scale-factor=1",
    "--font-render-hinting=none",
    "--disable-lcd-text",
    "--hide-scrollbars",
    "--mute-audio",
]

DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}
DEFAULT_TIMEOUT_MS = 30_000


async def render_html_to_image(
    html_path: str | Path,
    output_path: str | Path,
    full_page: bool = False,
    viewport: dict = None,
    jpeg_quality: int = 90,
) -> Path:
    """

    full_page=False (по умолчанию) — снимок только области viewport, для слайдов
    фиксированного размера (презентации, карточки).
    full_page=True — снимок ВСЕЙ высоты страницы, включая то, что за пределами
    viewport. Используй для длинных HTML-документов/отчётов, а НЕ для
    многослайдовых презентаций через page-break — каждый слайд презентации
    рендерь отдельным HTML-файлом и вызовом этой функции, склейку в PDF
    делай отдельно (см. dev_utils/slides_to_pdf.py).
    """
    viewport = viewport or DEFAULT_VIEWPORT
    html_path = Path(html_path).resolve()
    output_path = Path(output_path)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            executable_path=CHROMIUM_PATH,
            args=_LAUNCH_ARGS,
        )
        try:
            page = await browser.new_page(
                viewport=viewport,
                device_scale_factor=1,
                color_scheme="light",
            )
            page.set_default_timeout(DEFAULT_TIMEOUT_MS)
            page.set_default_navigation_timeout(DEFAULT_TIMEOUT_MS)

            await page.goto(f"file://{html_path}", wait_until="networkidle")
            await page.evaluate("document.fonts.ready")
            await page.emulate_media(media="screen")

            await page.screenshot(
                path=str(output_path),
                type="jpeg",
                quality=jpeg_quality,
                full_page=full_page,   # теперь реально используется параметр, не захардкожено
            )
        finally:
            await browser.close()

    return output_path