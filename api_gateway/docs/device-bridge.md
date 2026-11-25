# Device Bridge: ping + текстовые сообщения

Реализован транспорт в `gw_shared/device_bridge.py`: регистрация устройства,
WebSocket с bearer-аутентификацией, heartbeat, передача **`ping` и `notify`** и возврат
результата. Gateway сам ничего на устройстве не исполняет.

Main gateway и Sigma подключают одну фабрику
`create_router(get_current_user, redis_client)`. У каждого свой экземпляр сервиса,
но состояние общее в Redis. HTTP-запрос и WS могут попасть в разные процессы или
разные gateway, если у них одинаковые Redis и идентификатор текущего пользователя.
Sticky sessions не нужны; сам открытый WS остаётся на принявшем его процессе.

**Не реализованы:** исполняемый Windows-клиент, установка/автозапуск клиента,
agent tool, UI управления устройствами, shell/exec,
PowerShell, файловые операции. Изменений UI и схемы БД нет; Device Bridge не
хранит данные в SQL. Будущее выполнение программ — отдельный этап с отдельной
моделью разрешений и безопасности, а не скрытая возможность этого API.

## Развёртывание и доверенная граница

- Используйте HTTPS/WSS на том же домене, где открыт интерфейс и выполнен вход.
- Main и Sigma должны видеть общий `REDIS_URL` (по умолчанию
  `redis://redis:6379/0`), включая номер Redis DB. Нужен обычный **не cluster** Redis
  с поддержкой `EVAL`: атомарные Lua-операции не имеют небезопасного fallback.
- HTTP-владелец определяется только gateway через `get_current_user`:
  `authenticated: true` и непустой `user_id`. В тело запроса `user_id` не передают.
  Используются существующая login-cookie или доверенная SSO-идентичность.
- `X-Authentik-Username` нельзя принимать от внешнего клиента как доказательство
  личности. Ingress обязан удалить клиентские SSO-заголовки и выставить их заново
  **только из проверенного ответа SSO**. Это относится и к прочим
  `X-Authentik-*`, особенно в Sigma. Сам gateway доверяет этим заголовкам;
  прямой доступ извне к нему недопустим.
- Реальный порт Sigma — **7010**, main — 7000. При nginx на хосте оставьте
  публикацию Sigma `127.0.0.1:7010:7010`, не `0.0.0.0:7010:7010`.
  В контейнере uvicorn может слушать `0.0.0.0:7010`; внешняя граница — loopback
  публикации и firewall. Для контейнерного nginx используйте закрытую сеть.
- В deployment-аргументы uvicorn рекомендуется добавить
  `--ws websockets --ws-max-size 524288` (512 KiB). Прикладная проверка размера
  не предотвращает первичную буферизацию ASGI-сервером. Конфиги проекта этой
  инструкцией не изменяются. Корректно настройте доверенные proxy headers:
  схема/Host, видимые приложению, должны совпадать с публичным origin; доверять
  forwarded-заголовкам можно только от своего ingress.
- Не логируйте `Authorization`, cookies, ответ регистрации с токеном и полный
  дамп заголовков. Не передавайте токены в URL, query string, чат или скриншоты.

### nginx: исключение SSO только для device WebSocket

Пример для существующего HTTPS `server` того же домена; TLS и обычные HTTP/SSO
location остаются в действующей конфигурации. `map` находится в контексте `http`.
Пример направляет WS в Sigma; HTTP `/api/devices` может обслуживать main gateway
с тем же Redis. Это фрагмент конфигурации для адаптации, не установочный скрипт.

```nginx
# http {}
map $http_upgrade $device_bridge_connection {
    default upgrade;
    ''      close;
}

# В существующем HTTPS server {}
location = /api/devices/connect {
    # Только точное совпадение: НЕ отключать SSO для /api/devices или всего /api/.
    auth_request off;

    proxy_pass http://127.0.0.1:7010;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $device_bridge_connection;

    # Единственное удостоверение устройства; не заменять токеном SSO.
    proxy_set_header Authorization $http_authorization;
    proxy_set_header Cookie "";
    proxy_set_header X-Authentik-Username "";
    proxy_set_header X-Authentik-Uid "";
    proxy_set_header X-Authentik-Email "";
    proxy_set_header X-Authentik-Name "";
    proxy_set_header X-Authentik-Groups "";
    proxy_set_header X-Forwarded-User "";
    proxy_set_header Remote-User "";

    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_read_timeout 60s;
    proxy_send_timeout 60s;
    proxy_buffering off;
}
```

Если ingress использует другие identity-заголовки, их тоже следует очистить в
этом location. Проверьте итоговый набор `proxy_set_header`: директивы из родителя
не всегда наследуются при наличии локальных директив. Не переносите сюда SSO
redirect или подстановку `Authorization` из auth subrequest.

`auth_request off` **не означает анонимный WS**: gateway обязательно проверяет
единственный `Authorization: Bearer <device token>` и захватывает Redis lease
**до** accept. Cookies, SSO и query-параметры не используются для WS-авторизации.
Обычные HTTP API остаются защищены действующим входом и проверкой владельца.

## HTTP API и защита записи

| Метод и путь | Тело / результат |
| --- | --- |
| `POST /api/devices` | `{ "name": "Рабочий ПК", "platform": "windows" }` → 201, `{device_id, token, expires_in}` |
| `GET /api/devices` | `{devices: [{device_id, name, platform, status, created_at}]}` только текущего владельца; `status`: `online`, `offline`, `revoked`; `created_at`: Unix seconds |
| `POST /api/devices/{device_id}/commands` | `{ "operation": "ping", "timeout": 10 }` → ожидание `{job_id, status, output, exit_code?}` |
| `DELETE /api/devices/{device_id}` | Без тела → `{device_id, revoked: true}` |
| `DELETE /api/devices/{device_id}?offline_only=true` | Без тела → `{device_id, revoked: true}`; окончательно удаляет только offline-устройство |

Без query (или с `offline_only=false`) DELETE сохраняет legacy-отзыв: запись
остаётся в списке со статусом `revoked`. При `offline_only=true` Lua атомарно
проверяет владельца и актуальный lease/session: если устройство успело выйти
online, ответ **409** (`Device came online`), ничего не удаляется. Иначе удаляются
запись, token lookup, lease, очередь/pending и связанные задания, а ID убирается
из индекса владельца. Устройство исчезает из списка/UI, старый токен больше не
авторизует WS. Чужой ID и повторное удаление — **404**. Токен в ответ не включается.

`name`: 1–128 символов, `platform`: 1–64; строки не должны быть пустыми после
trim или содержать NUL. `platform` — метка, а не подтверждение совместимости ОС.
Для команды допускается только `operation: "ping"`; `timeout` — строго целое
**1..30 секунд**, по умолчанию 10. Лишние поля и преобразование типов запрещены:
`user_id`, `command`, `shell`, `args` и прочие произвольные поля не поддерживаются.

Для **каждого POST и DELETE** обязателен заголовок **`X-Device-Bridge: 1`**
(значение — ровно строка `1`, не `true`). JSON отправляйте с
`Content-Type: application/json`. Это защита записи, не замена аутентификации:

- `Sec-Fetch-Site: cross-site` отклоняются;
- если есть `Origin`, его схема и host:port должны точно совпадать с
  `request.base_url`; путь, query и fragment в origin запрещены;
- браузер сам выставляет `Origin`/`Sec-Fetch-Site`: не пытайтесь их подделывать.
  Отсутствие этих заголовков у небраузерного клиента не заменяет вход и
  обязательный `X-Device-Bridge: 1`;
- не ослабляйте проверки и не включайте доступ с произвольных origins для обхода
  403; сначала проверьте публичные Host/схему и доверие к proxy headers.

Успешные HTTP-ответы помечены `Cache-Control: no-store` и `Pragma: no-cache`.
Нет входа — 401; нарушение защиты записи — 403; чужое/неизвестное устройство —
404; невалидное тело — 422. Offline/обрыв — 409, очередь заполнена — 429,
дедлайн — 504, Redis недоступен — 503. `status: "error"` в успешном HTTP-ответе
означает результат устройства, а не HTTP-ошибку.

### Примеры из DevTools браузера

Откройте Console **на самом домене приложения после входа**, не на пустой
странице и не на стороннем сайте. Идентичность `current_user` подставляется
автоматически из существующей сессии; ни `user_id`, ни SSO-заголовки вручную не
задаются. Выполняйте по шагам, переменные сохраняйте в этой вкладке.

```javascript
async function deviceRequest(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    credentials: 'same-origin',
    cache: 'no-store'
  });
  if (!response.ok) throw new Error(`Device Bridge HTTP ${response.status}`);
  return await response.json();
}

// Регистрация: token возвращается только здесь. Не выводите весь registered.
const registered = await deviceRequest('/api/devices', {
  method: 'POST',
  headers: {'Content-Type': 'application/json', 'X-Device-Bridge': '1'},
  body: JSON.stringify({name: 'Windows / VM smoke', platform: 'windows'})
});
const deviceId = registered.device_id;
console.log({device_id: deviceId, expires_in: registered.expires_in});

// Список не содержит токенов.
const deviceList = await deviceRequest('/api/devices');
console.table(deviceList.devices);
```

Для следующего шага сначала подключите WS-инструмент, как описано ниже.
Используйте `registered.token` **только локально** для поля Authorization в
доверенном инструменте; не публикуйте значение и не вставляйте его в чат.
Ответ регистрации также виден в Network — не делитесь его дампом.

```javascript
// После WS hello: пока fetch ждёт, ответьте на command в WS-инструменте.
const pingResult = await deviceRequest(`/api/devices/${deviceId}/commands`, {
  method: 'POST',
  headers: {'Content-Type': 'application/json', 'X-Device-Bridge': '1'},
  body: JSON.stringify({operation: 'ping', timeout: 30})
});
console.log(pingResult);

// Отзыв после проверки; повторное подключение с тем же токеном запрещено.
const revoked = await deviceRequest(`/api/devices/${deviceId}`, {
  method: 'DELETE',
  headers: {'X-Device-Bridge': '1'}
});
console.log(revoked);
registered.token = ''; // Удалить также локальные копии в WS-инструменте.
```

## Протокол и transport-only smoke

Подойдёт любой **доверенный WebSocket-инструмент с поддержкой заголовка
Authorization** и отправки текстового JSON. Никакой shell, install script или
исполняемый клиент для этой проверки не требуется. Браузерный
`new WebSocket(...)` не позволяет выставить этот заголовок и здесь не подходит.
Не используйте веб-сервисы, которым пришлось бы отдать токен.

1. Зарегистрируйте устройство через HTTP выше. В инструменте откройте
   `wss://<домен-приложения>/api/devices/connect` без query string, с единственным
   заголовком `Authorization: Bearer <локальное значение registered.token>`.
   После `Bearer` — один пробел; без кавычек. Это **device token**, не login/SSO
   token. Второе одновременное подключение не вытесняет первое, а отклоняется.
2. Дождитесь `hello`. На каждый прикладной JSON `ping` отвечайте JSON `pong`
   с тем же nonce. Это не WS control frame ping/pong. Интервал — 5 секунд,
   допустимое ожидание pong — 15 секунд; для ручного теста отвечайте сразу.
3. В браузере отправьте HTTP-команду `operation: "ping"`. Получив `command`,
   верните `result` с **полученными** `job_id` и `session_id` до `deadline`.
   Это ручная имитация результата транспорта, не исполнение на ОС.
4. Проверьте HTTP-результат, затем отзовите устройство. Сокет должен закрыться,
   повторное подключение тем же токеном — не пройти. Новый тест требует новой
   регистрации. Без Authorization соединение также должно отклоняться, даже
   если есть SSO-cookie.

Пример обмена (UUID и nonce иллюстративные; в ответах используйте реальные):

```json
{"type":"hello","device_id":"11111111-1111-4111-8111-111111111111","session_id":"22222222-2222-4222-8222-222222222222","heartbeat_interval":5}
{"type":"ping","nonce":"example-nonce"}
{"type":"pong","nonce":"example-nonce"}
{"type":"command","job_id":"33333333-3333-4333-8333-333333333333","session_id":"22222222-2222-4222-8222-222222222222","deadline":1900000030.25,"operation":"ping","timeout":30}
{"type":"result","job_id":"33333333-3333-4333-8333-333333333333","session_id":"22222222-2222-4222-8222-222222222222","status":"ok","output":"pong","exit_code":0}
```

Направление: `hello`, `ping`, `command` — gateway → устройство;
`pong`, `result` — устройство → gateway. `deadline` — Unix seconds по часам
Redis. Результат допускает `status: "ok" | "error"`, строковый `output` и
необязательный целый `exit_code` (signed 32-bit). После успешной корреляции HTTP
вернёт, например:

```json
{"job_id":"33333333-3333-4333-8333-333333333333","status":"ok","output":"pong","exit_code":0}
```

Неизвестный, запоздалый или повторный результат получает
`{"type":"error","error":"result_not_pending"}`. Невалидный JSON/поля либо
нарушение протокола закрывают WS с 1008, превышение размера — 1009;
ошибка хранилища — 1011 (до accept возможен отказ handshake вместо close frame).
Только текстовые JSON-сообщения; `output` максимум 64 KiB UTF-8, всё сообщение
максимум 512 KiB, включая JSON escaping.

### Windows и VM: готовность, не подтверждение испытаний

Транспорт не зависит от ОС: на Windows-хосте и в Windows VM можно выполнить тот
же smoke доверенным WS-инструментом. Нужны доступ к домену по HTTPS/WSS,
доверенная цепочка TLS, разрешённый Upgrade через корпоративный proxy/firewall
и действующий device token. VM может подключаться наружу через NAT; входящий
порт на Windows открывать не нужно. Адрес `localhost` в VM относится к самой VM,
поэтому используйте публичный домен gateway.

**Локальная проверка на Windows/Windows VM не проводилась.** Это готовность
серверного протокола к подключению, не заявление о протестированном Windows
клиенте. Проверки исполнения программ, автозапуска и интеграции с агентом на
этом этапе невозможны: таких компонентов пока нет.

## Redis, отзыв и гарантии

- Токен случаен, возвращается однократно; в Redis сохраняется только его
  **SHA-256**, не исходный секрет. TTL токена и записи устройства — **30 дней**
  (`expires_in: 2592000`), heartbeat не продлевает срок credentials. Потерянный
  токен нельзя получить через список: нужна новая регистрация.
- Пространство ключей `gw:device-bridge:` не содержит PID/имя gateway:
  `device`, `token`, `owner` (хеш владельца), `lease`, `queue`, `pending`, `job`.
  Авторизация, lease, очередь и корреляция результатов живут в Redis, а не в
  памяти одного worker. Для независимых окружений используйте разные Redis DB
  или инстансы, иначе они намеренно разделяют это пространство.
- Lease эксклюзивен, TTL **5 секунд**, регулярно обновляется живым обработчиком.
  После аварийного завершения worker старый lease истекает; reconnect до его
  истечения может быть отклонён. Отзыв атомарно удаляет token lookup и lease;
  сокет замечает отзыв в течение примерно секунды плюс сетевое планирование.
- Задания привязаны к конкретной сессии и дедлайну, выдаются один раз и
  **не переигрываются после reconnect**. Очередь ограничена 100 outstanding
  заданиями на сессию. Старые/поддельные результаты не создают произвольных
  result-ключей; незавершённые задания очищаются/истекают.
- Отмена HTTP-ожидания удаляет outstanding job. Это **не гарантия отмены уже
  доставленной работы на устройстве**. Для будущего exec ни timeout, ни revoke,
  ни разрыв WS сами по себе не гарантируют остановку процесса; такой механизм
  здесь не реализован. Даже успешный ping проверяет транспорт, не возможности ОС.
- Redis недоступен — fail closed: HTTP 503 / WS ошибка, без локального обхода
  авторизации. Redis здесь хранит credentials, а не только восстанавливаемый
  кэш: eviction, flush или потеря недолговечных данных могут уничтожить
  регистрацию и доступ. Отсутствующие credentials отклоняются; автоматического
  восстановления токенов нет. Желателен **выделенный durable Redis** с persistence,
  резервным копированием и политикой без вытеснения credentials; одного TTL
  недостаточно для долговечности.

## Настольный клиент Windows (ping + notify)

Клиент с окном подключения находится в [device_client/README.md](../../device_client/README.md).
Требуется Python 3.10+ с Tk; запуск — `device_client/start-windows.ps1` либо
`python device_client/desktop.py` после установки device_client/requirements.txt.
Для notify обновите gateway и клиент; WSS endpoint остался прежним.
В поле Server — `https://augent.uva-advanced.ru`, в поле Device token —
значение `registered.token`, полученное при регистрации (не device_id).
Встроены heartbeat, ответ `pong`, переподключение и остановка при отказе авторизации.
Никаких shell, прав администратора, сохранения токена или открытых входящих портов.


## Notify и внутренний API для будущей тулы агента

Поддержаны public `POST /api/devices/{device_id}/commands` и service-only
`POST /internal/devices/{device_id}/commands`. Операции только `ping`/`notify`.
Для notify обязательный `text`: непустая строка до 2000 Unicode code points,
LF/CR/tab допустимы, остальные Cc/Cf/Cs запрещены. Payload не исполняется.
Client hello protocol1 объявляет capabilities; старый ping-only клиент получает
HTTP 409 для notify. ACK `displayed` означает принято в UI-очередь, **не прочитано**.
Timeout/disconnect не отменяют уже показанное сообщение; автоматического replay нет.

### Доверенная граница

`DEVICE_BRIDGE_SERVICE_TOKEN` — отдельный случайный секрет (32–512 printable ASCII
без пробелов, рекомендуется 64 hex chars). Пустой/некорректный секрет отключает
internal API (503). Должен совпадать у gateway и доверенного сервиса-потребителя.
Device bearer token для этого не подходит. Требуется ровно один
`Authorization: Bearer <service secret>`; cookies/SSO не заменяют его.
`user_id` берётся **только из серверного authenticated контекста запроса агента**,
никогда из ответа LLM, аргументов тулы или произвольного ввода браузера.
Сервисный credential разрешает делегирование любому owner: его утечка критична.
На данном этапе LLM tool ещё не подключена.

Trusted caller обращается напрямую по закрытой Docker-сети: `http://gateway:7000`.
Не публикуйте internal API через nginx. В server-блоках **обоих публичных vhost**
(main и Sigma, включая aliases) добавьте:

```nginx
location = /internal/devices { return 404; }
location ^~ /internal/devices/ { return 404; }
```

Это не замена bearer auth. Не логируйте Authorization, секрет или текст сообщений.
На хосте генерируйте секрет один раз в защищённый файл/secret store и настройте
`DEVICE_BRIDGE_SERVICE_TOKEN` в .env с ограниченными правами. Compose пробрасывает
его в оба gateway; после изменения окружения требуется пересоздание контейнеров.
Agent service пока не получает секрет — подключим вместе с реальной тулой.
Обновляйте **оба gateway и все workers одновременно**, перед обновлением завершите
активные bridge команды/соединения. Rolling/mixed-version notify не поддерживается.

### Контракт сервиса

- `GET /internal/devices?user_id=56` — список только этого owner, без токенов.
- `POST /internal/devices/{device_id}/commands` с JSON:

```json
{"user_id":56,"operation":"notify","text":"Привет с сервера","timeout":10}
```

`user_id` — положительный strict integer; чужое устройство — 404, offline или
legacy notify — 409, timeout — 504, неверный body — 422, Redis outage — 503.
Результат содержит `job_id`, `status`, `output`, `exit_code` (exit_code опционален у legacy ping).
Internal register/revoke отсутствуют. Public API owner-параметра не принимает.

### Ручной тест с авторизованной страницы

```javascript
await deviceRequest(`/api/devices/${deviceId}/commands`, {
  method: 'POST',
  headers: {'Content-Type':'application/json','X-Device-Bridge':'1'},
  body: JSON.stringify({operation:'notify', text:'Привет, Павел! Сообщение с сервера.', timeout:10})
});
```

Этот запрос инициирует delivery с gateway на ноутбук через уже открытый WSS,
несмотря на то, что запуск теста — из браузера. Настоящий internal smoke проверяет
тот же Redis relay с отдельным bearer, без пользовательских cookies.
