"""Monaco из образа отдаётся по /static/js/vendor/monaco и перекрывает /static (том с исходниками)."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

import cache_bust


def test_vendor_monaco_mounted_before_static(tmp_path):
    img = tmp_path / "img" / "vs"; img.mkdir(parents=True); (img / "loader.js").write_text("// image")
    old = tmp_path / "static" / "js" / "vendor" / "monaco" / "vs"; old.mkdir(parents=True)
    (old / "loader.js").write_text("// static")
    (tmp_path / "static" / "app.js").write_text("ok")
    app = FastAPI()
    assert cache_bust.mount_vendor_monaco(app, tmp_path / "img") is True
    app.mount("/static", cache_bust.CacheBustStaticFiles(directory=tmp_path / "static"), name="static")
    c = TestClient(app)
    assert c.get("/static/js/vendor/monaco/vs/loader.js").text == "// image"
    assert c.get("/static/app.js").text == "ok"


def test_missing_dir_is_not_mounted(tmp_path):
    assert cache_bust.mount_vendor_monaco(FastAPI(), tmp_path / "nope") is False
