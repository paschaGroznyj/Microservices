"""Сборка scrollytelling-презентации на реальных CSS/JS проекта.

dev:   python build.py          -> dist/index.html (ссылки на ../../../api_gateway/static)
dist:  python build.py --inline -> dist/augent_scrolly.html (всё вшито, шрифты base64)
"""
import json
import base64, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent.parent / "api_gateway" / "static"
DIST = HERE / "dist"

# Тот же порядок, что в templates/chat.html (подмножество, нужное для слайдов).
PROJECT_CSS = [
    "theme.css", "layout.css", "chat.css", "tools-panel.css", "query-mode.css",
    "task-cards.css", "flowmap.css", "file-attach.css", "skills.css",
    "claude-ui.css", "generative-ui.css", "presence.css",
]
PROJECT_JS = ["animations.js", "flowmap.js"]
ICONS = ["icon_black.png", "icon_white.png", "icon_sigma_black.png", "icon_sigma_white.png"]


def _font_data_uri(m, css_dir):
    p = (css_dir / m.group(1)).resolve()
    if not p.is_file():
        return m.group(0)
    return 'url("data:font/woff2;base64,' + base64.b64encode(p.read_bytes()).decode() + '")'


def project_css(inline):
    if not inline:
        return "\n".join(f'<link rel="stylesheet" href="../../../api_gateway/static/css/{n}">' for n in PROJECT_CSS)
    out = []
    for n in PROJECT_CSS:
        p = STATIC / "css" / n
        css = p.read_text(encoding="utf-8")
        css = re.sub(r'url\("?(\.\./fonts/[^")]+)"?\)', lambda m: _font_data_uri(m, p.parent), css)
        out.append(f"/* ── {n} ── */\n{css}")
    return "<style>\n" + "\n".join(out) + "\n</style>"


def project_js(inline):
    if not inline:
        return "\n".join(f'<script src="../../../api_gateway/static/js/{n}"></script>' for n in PROJECT_JS)
    return "\n".join(f"<script>/* {n} */\n{(STATIC / 'js' / n).read_text(encoding='utf-8')}\n</script>" for n in PROJECT_JS)


def icons(inline):
    res = {}
    for n in ICONS:
        p = STATIC / "img" / n
        key = n.rsplit(".", 1)[0]
        if inline and p.is_file():
            res[key] = "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
        else:
            res[key] = f"../../../api_gateway/static/img/{n}"
    return res


def build(inline):
    parts = sorted((HERE / "parts").glob("*.html"))
    body = "\n".join(p.read_text(encoding="utf-8") for p in parts)
    # снимок flow:{chat_id} (snap_flow.py) -> window.SC_FLOW; без снимка глава 3 играет демо-хронологию
    snaps = sorted((HERE / "data").glob("flow_*.json")) if (HERE / "data").is_dir() else []
    if snaps:
        snap = json.loads(snaps[-1].read_text(encoding="utf-8"))
        snap["key"] = "flow:{chat_id}"  # id чата в презентацию не попадает
        js = json.dumps(snap, ensure_ascii=False).replace("</", "<\\/")
        body = f"<script>window.SC_FLOW = {js};</script>\n" + body
    html = (HERE / "shell.html").read_text(encoding="utf-8")
    html = (html.replace("{{PROJECT_CSS}}", project_css(inline))
                .replace("{{OWN_CSS}}", "<style>\n" + (HERE / "scrolly.css").read_text(encoding="utf-8") + "\n</style>")
                .replace("{{BODY}}", body)
                .replace("{{PROJECT_JS}}", project_js(inline))
                .replace("{{OWN_JS}}", "<script>\n" + (HERE / "scrolly.js").read_text(encoding="utf-8") + "\n</script>"))
    for k, v in icons(inline).items():
        html = html.replace("{{" + k + "}}", v)
    DIST.mkdir(exist_ok=True)
    name = "augent_scrolly.html" if inline else "index.html"
    (DIST / name).write_text(html, encoding="utf-8")
    print(f"{name}: {len(html):,} bytes, parts={[p.name for p in parts]}")


if __name__ == "__main__":
    build("--inline" in sys.argv)
