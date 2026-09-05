# VideoDownloaderBot v0.1.1

Telegram-бот для скачивания доступных пользователю видео по ссылкам из Instagram и TikTok.

## Возможности

- Instagram Reels и видеопосты
- TikTok
- короткие `vm.tiktok.com` и `vt.tiktok.com` ссылки
- автоматическое определение платформы
- `yt-dlp` + FFmpeg
- Instagram-сессия через `.env` или `cookies.txt`
- ограничение параллельных загрузок
- автоматическая очистка временных файлов
- Docker / Docker Compose

> Используйте бот только для публично доступных материалов и контента, который вы имеете право скачивать. Бот не предназначен для обхода DRM или чужих закрытых публикаций.

## Docker

```bash
git pull
cp .env.example .env
nano .env
docker compose up -d --build
docker compose logs -f bot
```

Минимально в `.env`:

```env
BOT_TOKEN=ВАШ_ТОКЕН_ОТ_BOTFATHER
```

## Если Instagram отвечает ошибкой

Instagram нередко требует авторизованную сессию даже для публичных Reels. Войдите в свой Instagram в браузере и возьмите значения cookies:

- `sessionid` — основное
- `csrftoken` — желательно
- `ds_user_id` — желательно

Добавьте только **значения** в серверный `.env`:

```env
INSTAGRAM_SESSIONID=ваше_значение_sessionid
INSTAGRAM_CSRFTOKEN=ваше_значение_csrftoken
INSTAGRAM_DS_USER_ID=ваше_значение_ds_user_id
```

После изменения:

```bash
docker compose down
docker compose up -d --build
docker compose logs -f bot
```

Бот сам создаёт временный Netscape cookie-файл при запуске. Реальные cookies нельзя коммитить в GitHub или отправлять в чат.

### Альтернативный способ

Можно использовать готовый Netscape-format `cookies.txt`:

```env
COOKIES_FILE=/app/secrets/cookies.txt
```

и раскомментировать соответствующий volume в `docker-compose.yml`.

## Быстрый запуск в Windows

Требуется Python 3.11–3.13 и FFmpeg в `PATH`.

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
python bot.py
```

## Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---:|---|
| `BOT_TOKEN` | — | токен Telegram-бота |
| `DOWNLOAD_DIR` | `downloads` | каталог временных файлов |
| `MAX_UPLOAD_MB` | `49` | максимальный размер отправляемого файла |
| `MAX_CONCURRENT_DOWNLOADS` | `3` | одновременно выполняемых скачиваний |
| `INSTAGRAM_SESSIONID` | пусто | cookie `sessionid` вашего Instagram |
| `INSTAGRAM_CSRFTOKEN` | пусто | cookie `csrftoken` |
| `INSTAGRAM_DS_USER_ID` | пусто | cookie `ds_user_id` |
| `COOKIES_FILE` | пусто | альтернативный путь к Netscape cookies |
| `FFMPEG_LOCATION` | пусто | путь к FFmpeg, если он не в `PATH` |
