## Деплой на сервер (Docker Compose)

### Что нужно на сервере

- **Docker** и **Docker Compose plugin** (`docker compose`)
- Открытый наружу порт **только для `web`** (по умолчанию 8000), если он реально нужен
- Postgres/Redis наружу не публикуем

### Быстрый старт

1) Склонировать репозиторий и перейти в папку проекта:

```bash
git clone <YOUR_REPO_URL>
cd tg-balance-hub
```

2) Создать `.env` (можно взять за основу `.env.example`):

- **BOT_TOKEN**: токен бота
- **BOT_ADMINS**: id админов через запятую
- **DB_PASSWORD**: поставить сильный пароль
- **ENCRYPTION_KEY**: случайный длинный секрет (см. подсказку в `.env.example`)
- **INTERNAL_UPDATE_TOKEN**: длинный случайный токен (для `/internal/*` ручек)
- **WEB_DOMAIN**: домен (если используешь web часть)

3) Запуск:

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

4) Проверка:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f bot
```

### Обновление

```bash
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

### Примечания по безопасности

- **Postgres не должен быть доступен снаружи**: в `docker-compose.prod.yml` нет `ports:` у `postgres`.
- Если `web` не нужен — можно временно поднять только бота/инфру:

```bash
docker compose -f docker-compose.prod.yml up -d --build postgres redis bot worker
```

### Локальные скрейперы (одной командой)

Если используешь сервисы без API (AdminVPS/ATLEX) и обновляешь баланс с рабочего ПК,
можно запускать все локальные скрейперы одной командой:

```bash
python tools/run_local_scrapers.py
```

Выборочно:

```bash
python tools/run_local_scrapers.py --only adminvps,atlex
```

