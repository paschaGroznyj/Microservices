"""
Общий код api_gateway и api_gateway_sigma.

В контейнерах лежит в /app/gw_shared:
  - docker-compose.yml монтирует ./gw_shared:/app/gw_shared в оба gateway;
  - при сборке образа (prod и test) Dockerfile копирует его из именованного контекста
    gw_shared (build.additional_contexts в compose), поэтому образ работает и без volumes.
"""
