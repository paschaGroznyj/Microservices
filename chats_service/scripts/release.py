#!/usr/bin/env python3
"""
Выкатка релиза в модалку «Что нового».

Исходник релиза: releases/<версия>.yml в корне репо (только текст, в git).
Медиа (демо-ролики, картинки) в git не кладём: они лежат на хосте в
releases/media/<версия>/ (папка в .gitignore) и заливаются этим скриптом в OBS.

Запуск (внутри контейнера chats_service, ./releases смонтирован в /releases):
    docker compose exec chats_service python -m scripts.release v1.0.8 --dry-run
    docker compose exec chats_service python -m scripts.release v1.0.8
    docker compose exec chats_service python -m scripts.release v1.0.8 --publish
    docker compose exec chats_service python -m scripts.release v1.0.8 --unpublish
    docker compose exec chats_service python -m scripts.release --list

Что делает без флагов:
  1. читает и проверяет releases/<версия>.yml;
  2. для каждого ролика готовит webm + mp4 + постер webp (чего нет, делает ffmpeg);
  3. заливает в OBS под catchpm/updates/<версия>/<имя>.<sha10>.<ext> с ContentType и
     Cache-Control immutable; уже залитые ключи пропускает;
  4. считает thumbhash, ширину и высоту постера;
  5. пишет app_updates + app_update_items. Новый релиз создаётся черновиком
     (is_published=false), у существующего флаг публикации не трогаем.
Повторный запуск безопасен: ключи медиа зависят от содержимого, фичи релиза
перезаписываются целиком по версии.

Подробно: chats_service/docs/releases.md
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import obs_file_storage as obs  # noqa: E402
from app.core.thumbhash import image_meta  # noqa: E402

RELEASES_DIR = Path(os.getenv("RELEASES_DIR", "/releases"))
KINDS = ("new", "improve", "fix")
VIDEO_EXT = {".webm": "video/webm", ".mp4": "video/mp4", ".mov": "video/quicktime"}
IMAGE_EXT = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg",
             ".jpeg": "image/jpeg", ".gif": "image/gif"}
CACHE_CONTROL = "public, max-age=31536000, immutable"
POSTER_WIDTH = 960
ITEMS_DDL = """
    CREATE TABLE IF NOT EXISTS app_update_items (
        id        SERIAL PRIMARY KEY,
        update_id INTEGER NOT NULL REFERENCES app_updates(id) ON DELETE CASCADE,
        ord       INTEGER NOT NULL,
        kind      VARCHAR(16) NOT NULL CHECK (kind IN ('new', 'improve', 'fix')),
        title     TEXT NOT NULL,
        body      TEXT NOT NULL DEFAULT '',
        media     JSONB,
        UNIQUE (update_id, ord)
    )
"""


class ReleaseError(Exception):
    """Ошибка в yml или медиа: печатаем и выходим с кодом 2."""


@dataclass
class Upload:
    key: str
    body: bytes
    mime: str


@dataclass
class Plan:
    version: str
    title: str
    body: str
    released: datetime
    items: list[dict] = field(default_factory=list)
    uploads: list[Upload] = field(default_factory=list)


# ── yml ─────────────────────────────────────────────────────────────────────
def norm_version(v) -> str:
    v = str(v or "").strip()
    if not v:
        raise ReleaseError("version пустая")
    return v if v.lower().startswith("v") else "v" + v


def load_release(path: Path) -> dict:
    if not path.is_file():
        raise ReleaseError(f"нет файла {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ReleaseError(f"{path.name}: yml не разобран: {e}")
    if not isinstance(data, dict):
        raise ReleaseError(f"{path.name}: ожидался словарь верхнего уровня")
    rel = {
        "version": norm_version(data.get("version")),
        "title": str(data.get("title") or "").strip(),
        "body": str(data.get("body") or "").strip(),
        "date": data.get("date"),
        "items": [],
    }
    if not rel["title"]:
        raise ReleaseError(f"{path.name}: нужен title")
    d = rel["date"]
    if d is None:
        rel["date"] = date.today()
    elif isinstance(d, str):
        try:
            rel["date"] = date.fromisoformat(d)
        except ValueError:
            raise ReleaseError(f"{path.name}: date в формате ГГГГ-ММ-ДД, получено {d!r}")
    elif not isinstance(d, date):
        raise ReleaseError(f"{path.name}: date в формате ГГГГ-ММ-ДД")
    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        raise ReleaseError(f"{path.name}: нужен непустой список items")
    for n, it in enumerate(items, 1):
        if not isinstance(it, dict):
            raise ReleaseError(f"items[{n}]: ожидался словарь")
        kind = str(it.get("kind") or "").strip()
        if kind not in KINDS:
            raise ReleaseError(f"items[{n}]: kind одно из {', '.join(KINDS)}, получено {kind!r}")
        title = str(it.get("title") or "").strip()
        if not title:
            raise ReleaseError(f"items[{n}]: нужен title")
        for fld in ("media", "poster"):
            if it.get(fld) is not None and not isinstance(it.get(fld), str):
                raise ReleaseError(f"items[{n}]: {fld} — имя файла в папке медиа")
        rel["items"].append({"kind": kind, "title": title,
                             "body": str(it.get("body") or "").strip(),
                             "media": it.get("media"), "poster": it.get("poster")})
    if path.stem != rel["version"]:
        raise ReleaseError(f"{path.name}: version {rel['version']} не совпадает с именем файла")
    return rel


# ── медиа ───────────────────────────────────────────────────────────────────
def content_key(version: str, name: str, body: bytes) -> str:
    """updates/<версия>/<имя>.<sha10>.<ext>: новый файл — новый ключ, кэш навсегда."""
    stem, ext = os.path.splitext(name)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)[:60] or "media"
    # Тот же корневой префикс, что у файлов чатов: catchpm/updates/<версия>/...
    return (f"{obs.OBS_FILES_PREFIX}/updates/{version}/"
            f"{safe}.{hashlib.sha256(body).hexdigest()[:10]}{ext.lower()}")


def _ffmpeg(*args: str) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise ReleaseError(f"ffmpeg: {r.stderr.strip()[-400:]}")


def _sibling(src: Path, ext: str) -> Path | None:
    p = src.with_suffix(ext)
    return p if p.is_file() else None


def prepare_video(src: Path, tmp: Path, poster: Path | None) -> dict[str, Path]:
    """webm + mp4 + постер. Берём готовые файлы рядом (то же имя), остальное делает ffmpeg."""
    stem = src.stem
    webm = src if src.suffix.lower() == ".webm" else _sibling(src, ".webm")
    mp4 = src if src.suffix.lower() == ".mp4" else _sibling(src, ".mp4")
    even = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    if webm is None:
        webm = tmp / f"{stem}.webm"
        _ffmpeg("-i", str(src), "-an", "-vf", even, "-c:v", "libvpx-vp9",
                "-b:v", "0", "-crf", "36", "-row-mt", "1", str(webm))
    if mp4 is None:
        mp4 = tmp / f"{stem}.mp4"
        _ffmpeg("-i", str(src), "-an", "-vf", even, "-c:v", "libx264", "-crf", "26",
                "-preset", "slow", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4))
    if poster is None:
        poster = _sibling(src.with_name(stem + "_poster"), ".webp")
    if poster is None:
        poster = tmp / f"{stem}_poster.webp"
        _ffmpeg("-ss", "0.3", "-i", str(src), "-frames:v", "1",
                "-vf", f"scale='min({POSTER_WIDTH},iw)':-2", "-q:v", "82", str(poster))
    return {"webm": webm, "mp4": mp4, "poster": poster}


def build_plan(rel: dict, media_dir: Path, tmp: Path) -> Plan:
    plan = Plan(version=rel["version"], title=rel["title"], body=rel["body"],
                released=datetime.combine(rel["date"], datetime.min.time()).replace(hour=12))
    seen: set[str] = set()

    def add(path: Path, mime: str) -> tuple[str, str]:
        body = path.read_bytes()
        key = content_key(plan.version, path.name, body)
        if key not in seen:
            seen.add(key)
            plan.uploads.append(Upload(key, body, mime))
        return key, obs.public_url(key)

    for n, it in enumerate(rel["items"], 1):
        out = {"kind": it["kind"], "title": it["title"], "body": it["body"], "media": None}
        if it["media"]:
            src = media_dir / it["media"]
            if not src.is_file():
                raise ReleaseError(f"items[{n}]: нет файла {src}")
            ext = src.suffix.lower()
            poster = None
            if it["poster"]:
                poster = media_dir / it["poster"]
                if not poster.is_file():
                    raise ReleaseError(f"items[{n}]: нет постера {poster}")
            if ext in VIDEO_EXT:
                files = prepare_video(src, tmp, poster)
                k1, webm = add(files["webm"], "video/webm")
                k2, mp4 = add(files["mp4"], "video/mp4")
                k3, pst = add(files["poster"], "image/webp")
                meta = image_meta(files["poster"].read_bytes())
                media = {"type": "video", "webm": webm, "mp4": mp4, "poster": pst,
                         "keys": [k1, k2, k3]}
            elif ext in IMAGE_EXT:
                k1, url = add(src, IMAGE_EXT[ext])
                meta = image_meta(src.read_bytes())
                media = {"type": "image", "src": url, "keys": [k1]}
                if poster is not None:
                    k2, pst = add(poster, IMAGE_EXT.get(poster.suffix.lower(), "image/webp"))
                    media["poster"], media["keys"] = pst, [k1, k2]
            else:
                raise ReleaseError(f"items[{n}]: формат {ext} не поддерживается "
                                   f"(видео: {', '.join(VIDEO_EXT)}; картинки: {', '.join(IMAGE_EXT)})")
            for src_k, dst_k in (("thumbhash", "thumbhash"), ("width", "w"), ("height", "h")):
                if meta.get(src_k):
                    media[dst_k] = meta[src_k]
            out["media"] = media
        plan.items.append(out)
    return plan


# ── OBS и БД ────────────────────────────────────────────────────────────────
async def upload_all(uploads: list[Upload], exists=None, put=None) -> tuple[int, int]:
    """Заливает недостающие ключи. Возвращает (залито, пропущено)."""
    exists = exists or obs.exists
    put = put or obs.upload_bytes
    done = skipped = 0
    for u in uploads:
        if await exists(u.key):
            skipped += 1
            print(f"  = {u.key} (уже в OBS)")
            continue
        if not await put(u.key, u.body, content_type=u.mime, cache_control=CACHE_CONTROL):
            raise ReleaseError(f"не залит {u.key}, подробности в логе выше")
        done += 1
        print(f"  + {u.key} ({len(u.body) // 1024} КБ, {u.mime})")
    return done, skipped


async def write_db(conn, plan: Plan) -> tuple[int, bool, bool]:
    """Создаёт или обновляет релиз по версии. Возвращает (id, создан ли, опубликован ли)."""
    await conn.execute(ITEMS_DDL)
    async with conn.transaction():
        row = await conn.fetchrow(
            "SELECT id, is_published FROM app_updates WHERE version = $1 ORDER BY id LIMIT 1 FOR UPDATE",
            plan.version)
        if row:
            uid, published, created = row["id"], row["is_published"], False
            await conn.execute(
                "UPDATE app_updates SET title = $2, body = $3, created_at = $4 WHERE id = $1",
                uid, plan.title, plan.body, plan.released)
        else:
            uid = await conn.fetchval(
                """INSERT INTO app_updates (version, title, body, is_published, created_at)
                   VALUES ($1, $2, $3, FALSE, $4) RETURNING id""",
                plan.version, plan.title, plan.body, plan.released)
            published, created = False, True
        await conn.execute("DELETE FROM app_update_items WHERE update_id = $1", uid)
        await conn.executemany(
            """INSERT INTO app_update_items (update_id, ord, kind, title, body, media)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb)""",
            [(uid, i, it["kind"], it["title"], it["body"],
              json.dumps(it["media"], ensure_ascii=False) if it["media"] else None)
             for i, it in enumerate(plan.items)])
    return uid, created, published


async def set_published(conn, version: str, flag: bool) -> int:
    uid = await conn.fetchval(
        "UPDATE app_updates SET is_published = $2 WHERE version = $1 RETURNING id", version, flag)
    if uid is None:
        raise ReleaseError(f"релиза {version} нет в БД: сначала запусти без --publish")
    return uid


async def list_releases(conn) -> list:
    await conn.execute(ITEMS_DDL)
    return await conn.fetch("""
        SELECT au.id, au.version, au.title, au.is_published, au.created_at,
               (SELECT count(*) FROM app_update_items i WHERE i.update_id = au.id) AS items
        FROM app_updates au ORDER BY au.created_at DESC, au.id DESC LIMIT 30""")


async def connect():
    import asyncpg
    return await asyncpg.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT") or 5432),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"))


# ── CLI ─────────────────────────────────────────────────────────────────────
def print_plan(plan: Plan) -> None:
    print(f"Релиз {plan.version} от {plan.released:%d.%m.%Y}: {plan.title}")
    for it in plan.items:
        m = it["media"]
        tail = f" [{m['type']}, {m.get('w', '?')}×{m.get('h', '?')}]" if m else ""
        print(f"  {it['kind']:<8} {it['title']}{tail}")
    size = sum(len(u.body) for u in plan.uploads)
    print(f"Медиа: {len(plan.uploads)} файлов, {size // 1024} КБ")


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Выкатка релиза в «Что нового»")
    ap.add_argument("version", nargs="?", help="например v1.0.8")
    ap.add_argument("--file", type=Path, help="yml релиза (по умолчанию /releases/<версия>.yml)")
    ap.add_argument("--media-dir", type=Path, help="папка медиа (по умолчанию /releases/media/<версия>)")
    ap.add_argument("--dry-run", action="store_true", help="только проверить yml и медиа, ничего не писать")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--publish", action="store_true", help="показать релиз пользователям")
    g.add_argument("--unpublish", action="store_true", help="спрятать релиз обратно в черновик")
    g.add_argument("--list", action="store_true", help="последние релизы в БД")
    a = ap.parse_args(argv)
    try:
        if a.list:
            conn = await connect()
            try:
                for r in await list_releases(conn):
                    st = "опубликован" if r["is_published"] else "черновик"
                    print(f"{r['id']:>4}  {r['version'] or '—':<10} {r['created_at']:%d.%m.%Y}  "
                          f"{st:<11} фич: {r['items']:<2} {r['title']}")
            finally:
                await conn.close()
            return 0
        if not a.version:
            ap.error("укажи версию, например v1.0.8")
        version = norm_version(a.version)
        if a.publish or a.unpublish:
            conn = await connect()
            try:
                uid = await set_published(conn, version, a.publish)
            finally:
                await conn.close()
            print(f"{version} (id={uid}): {'опубликован' if a.publish else 'снят в черновик'}")
            return 0
        rel = load_release(a.file or RELEASES_DIR / f"{version}.yml")
        if rel["version"] != version:
            raise ReleaseError(f"в yml версия {rel['version']}, а запрошена {version}")
        with tempfile.TemporaryDirectory(prefix="release_") as tmp:
            plan = build_plan(rel, a.media_dir or RELEASES_DIR / "media" / version, Path(tmp))
            print_plan(plan)
            if a.dry_run:
                print("dry-run: в OBS и БД ничего не записано")
                return 0
            done, skipped = await upload_all(plan.uploads)
            print(f"OBS: залито {done}, уже было {skipped}")
        conn = await connect()
        try:
            uid, created, published = await write_db(conn, plan)
        finally:
            await conn.close()
        state = "опубликован" if published else "черновик, покажи командой --publish"
        print(f"БД: app_updates id={uid} {'создан' if created else 'обновлён'}, {state}")
        return 0
    except ReleaseError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
