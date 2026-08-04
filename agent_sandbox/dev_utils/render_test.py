#!/usr/bin/env python3
"""
render_test.py - расширенная проверка render.py внутри sandbox-контейнера

Проверяет:
  0. Реальные лимиты контейнера (cgroup memory/cpu)
  1. Импорт render.py
  2. Лёгкий слайд (градиенты + сетка)
  3. Тяжёлый слайд (кастомный шрифт + несколько градиентов/теней + inline SVG)
  4. 5 последовательных рендеров подряд (утечки памяти/процессов между вызовами)
  5. Пиковую память дочерних процессов (RSS)
  6. Отсутствие зомби-процессов Chromium после закрытия браузера

Запуск: python3 /tmp/output/render_test.py
"""
import asyncio
import os
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "/app/dev_utils")

RENDER_TIMEOUT_S = 25
N_CONSECUTIVE = 5
MIN_OK_SIZE_KB = 50

# ── Лёгкий слайд ──────────────────────────────────────────────────
TEST_HTML_LIGHT = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  width: 1920px; height: 1080px; overflow: hidden;
  background: #F5F0E8; font-family: Georgia, serif;
}
.bg {
  position: absolute; inset: 0;
  background:
    radial-gradient(ellipse 1000px 700px at 8%  50%, rgba(217,119,6,.15) 0%, transparent 65%),
    radial-gradient(ellipse 800px  600px at 92% 15%, rgba(146,64,14,.12) 0%, transparent 65%),
    #F5F0E8;
}
.grid {
  position: absolute; inset: 0;
  background-image:
    linear-gradient(rgba(150,120,80,.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(150,120,80,.05) 1px, transparent 1px);
  background-size: 60px 60px;
}
.accent {
  position: absolute; left: 0; top: 0; bottom: 0; width: 6px;
  background: linear-gradient(180deg, #D97706, #92400E 50%, #D97706);
}
.topbar {
  position: absolute; top: 0; left: 6px; right: 0; height: 52px;
  background: rgba(245,240,232,.96); border-bottom: 1px solid rgba(180,140,80,.25);
  display: flex; align-items: center; padding: 0 24px; gap: 10px;
}
.dot { width: 13px; height: 13px; border-radius: 50%; }
.center {
  position: absolute; inset: 52px 0 48px 6px;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 28px;
}
h1 { font-size: 72px; color: #1C1410; font-weight: 400; letter-spacing: -2px; }
.sub { font-family: monospace; font-size: 22px; color: #92400E; opacity: .75; }
.badge {
  display: inline-flex; align-items: center; gap: 10px;
  background: rgba(217,119,6,.12); border: 1px solid rgba(217,119,6,.35);
  border-radius: 8px; padding: 10px 24px;
  font-family: monospace; font-size: 16px; color: #92400E;
}
.footer {
  position: absolute; bottom: 0; left: 6px; right: 0; height: 48px;
  border-top: 1px solid rgba(180,140,80,.2);
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 40px;
  font-family: monospace; font-size: 13px; color: rgba(92,64,30,.5);
}
</style>
</head>
<body>
<div class="bg"></div>
<div class="grid"></div>
<div class="accent"></div>
<div class="topbar">
  <div class="dot" style="background:#FF5F57"></div>
  <div class="dot" style="background:#FEBC2E"></div>
  <div class="dot" style="background:#28C840"></div>
  <span style="font-family:monospace;font-size:13px;color:#92400E;margin-left:12px;opacity:.7">
    ~/dev_utils/render_test.py
  </span>
</div>
<div class="center">
  <div class="badge">render.py · 1920x1080 · Claude Code style</div>
  <h1>Тест рендера прошёл успешно</h1>
  <div class="sub">Playwright · headless Chromium · JPEG 92%</div>
</div>
<div class="footer">
  <span>render_test.py</span>
  <span>1920 x 1080 px</span>
</div>
</body>
</html>"""

# ── Тяжёлый слайд: кастомный шрифт + несколько градиентов/теней + SVG ──
TEST_HTML_HEAVY = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
@font-face {
  font-family: "anthropicSerif";
  src: url("file:///app/fonts/anthropicSerif-Regular.woff2") format("woff2"),
       url("file:///app/fonts/anthropicSerif-Regular.ttf") format("truetype");
  font-weight: 400;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  width: 1920px; height: 1080px; overflow: hidden;
  background: #F5F0E8;
  font-family: "anthropicSerif", Georgia, serif;
}
.bg {
  position: absolute; inset: 0;
  background:
    radial-gradient(ellipse 900px 700px at 10% 20%, rgba(217,119,6,.18) 0%, transparent 60%),
    radial-gradient(ellipse 700px 600px at 90% 10%, rgba(146,64,14,.14) 0%, transparent 60%),
    radial-gradient(ellipse 1200px 800px at 50% 100%, rgba(28,20,16,.06) 0%, transparent 70%),
    #F5F0E8;
}
.cards {
  position: absolute; inset: 140px 80px 120px 80px;
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 28px;
}
.card {
  background: rgba(255,255,255,.55);
  border: 1px solid rgba(180,140,80,.25);
  border-radius: 18px;
  box-shadow: 0 12px 28px rgba(90,60,20,.15), 0 2px 6px rgba(90,60,20,.08);
  backdrop-filter: blur(6px);
  padding: 32px;
  display: flex; flex-direction: column; gap: 16px;
}
.icon { width: 48px; height: 48px; }
.title { font-size: 22px; color: #1C1410; }
.terminal {
  position: absolute; left: 80px; right: 80px; bottom: 40px; height: 220px;
  background: #1C1410; border-radius: 14px;
  box-shadow: 0 20px 40px rgba(0,0,0,.25);
  padding: 24px; font-family: monospace; color: #D97706; font-size: 16px;
}
</style>
</head>
<body>
<div class="bg"></div>
<div class="cards">
  <div class="card">
    <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="#D97706" stroke-width="1.5">
      <circle cx="12" cy="12" r="9"/>
      <path d="M8 12l3 3 5-6"/>
    </svg>
    <div class="title">Карточка 1</div>
  </div>
  <div class="card">
    <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="#92400E" stroke-width="1.5">
      <rect x="4" y="4" width="16" height="16" rx="3"/>
      <path d="M4 9h16M9 4v16"/>
    </svg>
    <div class="title">Карточка 2</div>
  </div>
  <div class="card">
    <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="#D97706" stroke-width="1.5">
      <path d="M12 3v18M3 12h18"/>
    </svg>
    <div class="title">Карточка 3</div>
  </div>
  <div class="card">
    <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="#92400E" stroke-width="1.5">
      <path d="M4 20l6-16 4 10 3-6 3 12"/>
    </svg>
    <div class="title">Карточка 4</div>
  </div>
</div>
<div class="terminal">$ render --style=claude-code --viewport=1920x1080 --quality=92</div>
</body>
</html>"""


def print_cgroup_limits():
    print("[0/6] Лимиты контейнера:")
    candidates_mem = [
        "/sys/fs/cgroup/memory.max",                 # cgroup v2
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",  # cgroup v1
    ]
    candidates_cpu = [
        "/sys/fs/cgroup/cpu.max",                    # cgroup v2
        "/sys/fs/cgroup/cpu/cpu.cfs_quota_us",        # cgroup v1
    ]
    for path in candidates_mem:
        if os.path.exists(path):
            with open(path) as f:
                val = f.read().strip()
            try:
                mb = int(val) / (1024 * 1024)
                print(f"      memory limit ({path}): {val}  (~{mb:.0f} MB)")
            except ValueError:
                print(f"      memory limit ({path}): {val}")
            break
    else:
        print("      memory limit: не найден (проверь путь cgroup вручную)")

    for path in candidates_cpu:
        if os.path.exists(path):
            with open(path) as f:
                print(f"      cpu limit ({path}): {f.read().strip()}")
            break
    else:
        print("      cpu limit: не найден (проверь путь cgroup вручную)")


def check_zombie_chromium():
    result = subprocess.run(
        ["pgrep", "-af", "chromium"], capture_output=True, text=True
    )
    zombies = result.stdout.strip()
    if zombies:
        print(f"      ⚠️  Chromium процессы всё ещё живы после close():\n{zombies}")
        return False
    print("      Chromium процессы корректно завершены")
    return True


async def render_one(render_fn, html_str: str, out_path: Path, quality: int = 92, label: str = ""):
    tmp = tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, mode="w", encoding="utf-8", dir="/tmp"
    )
    tmp.write(html_str)
    tmp.close()

    t0 = time.monotonic()
    try:
        await asyncio.wait_for(
            render_fn(tmp.name, out_path, jpeg_quality=quality),
            timeout=RENDER_TIMEOUT_S,
        )
        elapsed = time.monotonic() - t0
        kb = out_path.stat().st_size // 1024
        print(f"      {label}: OK  {kb} KB  ({elapsed:.1f}s)")
        return True, kb, elapsed
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - t0
        print(f"      {label}: FAIL — таймаут после {elapsed:.1f}s (похоже на OOM/CPU-килл Chromium)")
        return False, 0, elapsed
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"      {label}: FAIL — {e}")
        return False, 0, elapsed
    finally:
        os.unlink(tmp.name)


async def main():
    failures = []

    print_cgroup_limits()

    print("\n[1/6] Импортируем render.py ...", end=" ", flush=True)
    try:
        from render import render_html_to_image
        print("OK")
    except Exception as e:
        print(f"FAIL\n  {e}")
        sys.exit(1)

    print("\n[2/6] Лёгкий слайд (градиенты + сетка) ...")
    ok, kb, _ = await render_one(
        render_html_to_image, TEST_HTML_LIGHT,
        Path("/tmp/render_test_light.jpg"), label="light"
    )
    if not ok or kb < MIN_OK_SIZE_KB:
        failures.append("light slide")

    print("\n[3/6] Тяжёлый слайд (шрифт + blur/shadow + SVG) ...")
    ok, kb, _ = await render_one(
        render_html_to_image, TEST_HTML_HEAVY,
        Path("/tmp/render_test_heavy.jpg"), label="heavy"
    )
    if not ok or kb < MIN_OK_SIZE_KB:
        failures.append("heavy slide")

    print(f"\n[4/6] {N_CONSECUTIVE} последовательных рендеров подряд (утечки между вызовами) ...")
    times = []
    for i in range(N_CONSECUTIVE):
        ok, kb, elapsed = await render_one(
            render_html_to_image, TEST_HTML_HEAVY,
            Path(f"/tmp/render_test_seq_{i}.jpg"), label=f"#{i}"
        )
        if not ok:
            failures.append(f"consecutive render #{i}")
        times.append(elapsed)

    if len(times) >= 2 and times[-1] > times[0] * 2:
        print(f"      ⚠️  Время рендера выросло с {times[0]:.1f}s до {times[-1]:.1f}s — возможна деградация/утечка")

    print("\n[5/6] Пиковая память дочерних процессов ...")
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    print(f"      Peak RSS дочерних процессов: {ru.ru_maxrss / 1024:.0f} MB")

    print("\n[6/6] Проверка зомби-процессов Chromium ...")
    if not check_zombie_chromium():
        failures.append("zombie chromium processes")

    print("\n" + "=" * 50)
    if not failures:
        print("Все проверки пройдены. render.py готов к использованию на 1920x1080.")
    else:
        print(f"Провалено проверок: {len(failures)}")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)


asyncio.run(main())