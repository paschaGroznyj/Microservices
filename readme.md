"""
## 🚀 Запуск проекта

1. Поднятие инфраструктуры
   - Запустить Kafka:
     docker-compose up

2. Запуск микросервисов
   - Сервис отправки сообщений
     • FastAPI: chats_service/app.py
     • Kafka Consumer (запись в БД): chats_service/endpoint_consumer.py

   - Сервис авторизации
     • auth_service/app_cookie.py

   - Сервис бота (LLM)
     • llm_model/app.py

   - API Gateway
     • api_gateway/app_api.py

   - Сервис получения курсов валют ЦБ
     • Отдельный сервис на Java


## 🧩 Архитектура микросервисов и Kafka


```
                          ┌──────────────────────┐
                          │      Frontend        │
                          │ (SPA / HTML + JS)    │
                          └─────────▲────────────┘
                                    │
                                    │ HTTP
                                    │
                          ┌─────────┴────────────┐
                          │     API Gateway      │
                          │  (FastAPI)           │
                          └─────────▲────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
        │                           │                           │
        v                           v                           v
┌───────────────┐        ┌────────────────┐        ┌──────────────────┐
│ Auth Service  │        │ Chats Service  │        │   LLM Service    │
│ (Auth + JWT)  │        │ (Send Message) │        │ (Bot / Agent)    │
└──────▲────────┘        └───────▲────────┘        └──────▲───────────┘
       │                           │                           │
       │                           │                           │
       │                           │ Kafka PRODUCER            │ Kafka CONSUMER
       │                           │ (user message)            │ (user message)
       │                           │                           │
       │                           v                           │
       │                  ┌───────────────────┐               │
       │                  │       Kafka       │◄──────────────┘
       │                  │  topic: chat-messages  │
       │                  └─────────▲─────────┘
       │                            │
       │                            │ Kafka CONSUMER
       │                            │ (same Chats Service)
       │                            │
       │                            v
       │                   ┌──────────────────┐
       │                   │ Chats DB Writer  │
       │                   │ (consumer)       │
       │                   └─────────▲────────┘
       │                             │
       │                             │ INSERT
       │                             │
       │                         ┌────────┐
       │                         │  DB    │
       │                         │ Chats  │
       │                         └────────┘
       │
       │
       │            Kafka PRODUCER (bot answer)
       │        ┌─────────────────────────────────┐
       │        │                                 │
       │        v                                 │
       │  ┌───────────────────┐                 │
       │  │       Kafka       │◄─────────────────┘
       │  │ topic: bot-reply  │
       │  └─────────▲─────────┘
       │            │
       │            │ Kafka CONSUMER
       │            │ (Chats Service)
       │            v
       │   ┌────────────────────────┐
       │   │ Chats Service Consumer │
       │   │ + WebSocket broadcast  │
       │   └─────────▲──────────────┘
       │             │
       │             │ WebSocket
       │             │
       └─────────────┴──────────────► Frontend
```

## 🔁 Поток сообщений (по шагам)

1. Пользователь отправляет сообщение
   Frontend + API Gateway → Chats Service

2. Chats Service
   - сразу отправляет сообщение в WebSocket (optimistic UI)
   - PRODUCER: кладёт сообщение пользователя в Kafka (topic: chat-messages)

3. Chats Service (Consumer)
   - читает Kafka
   - сохраняет сообщение пользователя в БД

4. LLM Service (Consumer)
   - читает то же сообщение из Kafka
   - обрабатывает через LLM
   - PRODUCER: кладёт ответ бота в Kafka

5. Chats Service (Consumer)
   - читает сообщение бота из Kafka
   - сохраняет в БД
   - отправляет ответ пользователю через WebSocket


## 🧠 Ключевая идея

- Kafka — центральная шина событий
- Chats Service:
  • producer (user message)
  • consumer (DB + WebSocket)
- LLM Service:
  • consumer (user message)
  • producer (bot answer)
- Frontend ничего не знает про Kafka
- API Gateway — только HTTP-прокси
"""
