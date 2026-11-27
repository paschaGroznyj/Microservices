# Group chat UI regression harnesses

Run from repository root with Python 3 + playwright, beautifulsoup4, jinja2,
reportlab; Chromium installed through Playwright.

- `python3 pvport/test_gc_fixes.py`: actual template/CSS/JS, own/other mixed
  attachment bubbles, per-bubble metadata, caption color, max three reactions,
  deselect, full-list POST and failed-request retry, stale/exact server echo,
  context menu highlights, group file preview. 1440/390, light/dark.
- `python3 pvport/test_reorder.py`: actual FileAttach/FileTiles and CSS in
  reorder.html; native mouse drag, real CDP touch, keyboard, remove/add/dedup,
  external file drop, async upload snapshot order, composer context switch,
  history isolation, edge scrolling, Escape cleanup. Same viewport/theme matrix.

All files/content are explicit test fixtures. No production backend/DB is used.
Screenshots and reports go to output/gc_fixes and output/gc_fixes_reorder.
