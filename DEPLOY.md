## Деплой на сервер (Docker Compose)

### Рекомендуемый git-флоу (develop → prod)

- **Разработка**: всё делаем в ветке `develop`.
- **Прод**: сервер обновляется **только** из ветки `prod`.
- **Перед деплоем**: переносим изменения из `develop` в `prod` (merge или cherry-pick), пушим `prod`, и только потом обновляем сервер.

Полезные команды локально (перед деплоем):

```bash
# забрать свежие изменения
git fetch origin

# обновить develop
git checkout develop
git pull origin develop

# обновить prod и перенести изменения
git checkout prod
git pull origin prod

# вариант 1 (проще): влить develop в prod
git merge --no-ff develop

# вариант 2 (точечно): взять один коммит из develop в prod
# git cherry-pick <commit_sha>

# отправить prod на удалённый репозиторий
git push origin prod
```

### Что нужно на сервере

- **Docker** и **Docker Compose plugin** (`docker compose`)
- Открытый наружу порт **только для `web`** (по умолчанию 8000), если он реально нужен
- Postgres/Redis наружу не публикуем

### Быстрый старт

1) Склонировать репозиторий и перейти в папку проекта:

```bash
git clone <YOUR_REPO_URL>
cd tg-balance-hub

# сервер должен работать от ветки prod
git checkout prod
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
git fetch origin
git checkout prod
git pull origin prod
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

