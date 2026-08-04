"""
dev_utils/slides_to_pdf.py
"""

import sys
import os
import tempfile
import shutil

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from dev_utils.render import render_html_to_image
from PIL import Image


async def render_slides_to_pdf(
    html_paths: list[str],
    output_pdf_path: str,
    viewport: dict = None,
    dpi: tuple = (150, 150),
) -> dict:
    """
    Собирает PDF из списка HTML-файлов, каждый — отдельный слайд.
    Промежуточные JPEG удаляются автоматически — в saved_files попадает
    только итоговый PDF, слайды-превью не сохраняются.

    Аргументы:
        html_paths: список путей к HTML-файлам (относительные, от текущей
                    рабочей директории исполнения), по одному на слайд, по порядку
        output_pdf_path: путь для сохранения итогового PDF, например "output/presentation.pdf"
        viewport, dpi: см. render_html_to_image

    Возвращает: {"ok": bool, "output_path": str, "slides_count": int, "error": str|None}
    """
    if not html_paths:
        return {"ok": False, "error": "html_paths пуст", "output_path": None, "slides_count": 0}

    os.makedirs(os.path.dirname(output_pdf_path) or ".", exist_ok=True)

    # Промежуточные JPEG — в отдельную временную папку, НЕ в output/,
    # чтобы sandbox-сервис их не подхватил как saved_files
    work_dir = tempfile.mkdtemp(prefix="_slides_")
    images = []

    try:
        for i, html_path in enumerate(html_paths):
            img_path = os.path.join(work_dir, f"slide_{i}.jpeg")
            await render_html_to_image(html_path, img_path, full_page=False, viewport=viewport)
            images.append(Image.open(img_path).convert("RGB"))

        if not images:
            return {"ok": False, "error": "Ни один слайд не отрендерился", "output_path": None, "slides_count": 0}

        images[0].save(
            output_pdf_path,
            "PDF",
            save_all=True,
            append_images=images[1:],
            resolution=dpi[0],
        )
        return {"ok": True, "output_path": output_pdf_path, "slides_count": len(images), "error": None}

    except Exception as e:
        return {"ok": False, "error": str(e), "output_path": None, "slides_count": 0}

    finally:
        for img in images:
            img.close()
        shutil.rmtree(work_dir, ignore_errors=True)