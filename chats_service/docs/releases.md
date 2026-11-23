# Как выкатить обновление в «Что нового»

Модалка показывает релизы так: на ПК сплит (слева фичи, справа демо), на мобиле лента. Новый релиз открывается сам один раз после входа. Все прошлые релизы открываются кнопкой «Обновления» (звёздочка) в шапке профиля.

## Где что лежит

| Что | Где |
|---|---|
| Текст релиза | `releases/<версия>.yml` в git |
| Ролики и картинки | `releases/media/<версия>/` на хосте, в git не попадают (`.gitignore`) |
| Медиа в проде | OBS, открытый бакет: `catchpm/updates/<версия>/<имя>.<sha10>.<ext>` |
| Записи | `app_updates` (релиз) и `app_update_items` (фичи), база auth |
| Скрипт | `chats_service/scripts/release.py`, запускается в контейнере chats_service |
| Вёрстка | `api_gateway/static/js/whatsnew-releases.js` и `css/whatsnew-releases.css` |

Каталог `./releases` смонтирован в chats_service как `/releases` (только чтение).

## Шаги

1. **Медиа.** Положи файлы в `releases/media/v1.0.9/` на хосте:
   - видео: `name.webm` и/или `name.mp4` (или исходник `.mov`). Чего не хватает, сделает ffmpeg. Постер можно положить рядом как `name_poster.webp`, иначе он снимется с 0,3 с ролика;
   - картинки: `.webp`, `.png`, `.jpg`, `.gif`.
   Ролик держи коротким: 8–15 с, ширина до 960 px, без звука. Получается 100–600 КБ.

2. **Текст.** Создай `releases/v1.0.9.yml`:
   ```yaml
   version: v1.0.9          # совпадает с именем файла
   date: 2026-12-05
   title: Короткий заголовок релиза
   body: |                  # запасной markdown для старых клиентов
     - пункт
   items:
     - kind: new            # new | improve | fix
       title: Название фичи
       body: Что это и зачем, 1–3 предложения. Можно **markdown**.
       media: feature.webm  # имя файла в releases/media/v1.0.9/, необязательно
       poster: feature_poster.webp   # необязательно
     - kind: fix
       title: Что починили  # fix без media собираются в один блок «Исправления»
   ```
   Закоммить yml и запушь.

3. **Проверка без записи:**
   ```bash
   git pull
   docker compose exec chats_service python -m scripts.release v1.0.9 --dry-run
   ```
   Скрипт проверит yml и файлы, сделает недостающие webm/mp4/постеры и напечатает план.

4. **Заливка черновиком:**
   ```bash
   docker compose exec chats_service python -m scripts.release v1.0.9
   ```
   Медиа уйдут в OBS с правильным Content-Type и `Cache-Control: public, max-age=31536000, immutable`. В БД релиз появится с `is_published=false`.

5. **Посмотреть черновик.** Черновики видят только пользователи из `WHATSNEW_PREVIEW_USERS` (id через запятую в `.env`, сервис auth). Открой профиль → «Обновления»: у релиза будет плашка «Черновик».

6. **Опубликовать:**
   ```bash
   docker compose exec chats_service python -m scripts.release v1.0.9 --publish
   ```
   С этого момента каждый пользователь при следующем входе увидит релиз один раз.

## Другие команды

```bash
... python -m scripts.release --list                # последние 30 релизов и их статус
... python -m scripts.release v1.0.9 --unpublish    # снять в черновик
... python -m scripts.release v1.0.9 --file /releases/x.yml --media-dir /releases/media/x
```

## Правки после выкатки

Поменял текст или ролик: запусти шаг 4 ещё раз. Фичи релиза перезапишутся по версии, флаг публикации не сбросится. Изменённый файл получит новый ключ (хэш в имени), старый останется в OBS и никому не мешает. Неизменённые файлы повторно не заливаются.

## Как это работает в браузере

- При входе `whatsnew.js` делает один запрос `/api/whatsnew/pending`. Если там есть непросмотренный релиз, лениво подгружаются `whatsnew-releases.js/css` и `/api/whatsnew/history`.
- Медиа берутся прямо из OBS тегами `<img>`/`<video>` без `crossorigin`, поэтому CORS у бакета не нужен.
- Порядок загрузки: thumbhash (или пульс) → постер → видео. У видео `preload="none"`, играет только выбранная или видимая фича.
- Старые релизы без фич показываются как раньше, текстом из `app_updates.body`.

## Переменные окружения

| Переменная | Сервис | Зачем |
|---|---|---|
| `OBS_OPEN_BUCKET`, `OBS_ENDPOINT`, ключи OBS | chats_service | куда заливать |
| `OBS_PUBLIC_BASE` | chats_service | база прямых ссылок, если отличается от `https://<бакет>.<хост>`. Ссылка: `<база>/catchpm/updates/…` |
| `WHATSNEW_PREVIEW_USERS` | auth_service | кто видит черновики |
| `DB_*` | chats_service | та же база, где `app_updates` |

## Если что-то пошло не так

- `нет файла …`: имя в `media:` не совпадает с файлом в `releases/media/<версия>/`.
- `ffmpeg: …`: исходник битый или кодек не поддержан. Положи готовые `.webm` и `.mp4`.
- Релиз не показывается: проверь `--list` (опубликован ли) и что пользователь его ещё не видел (`user_seen_updates`).
- В профиле нет медиа, только текст: у релиза нет строк в `app_update_items`. Запусти шаг 4.
