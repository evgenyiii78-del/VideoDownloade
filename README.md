# VideoDownloaderBot v0.1.0

Telegram-бот для скачивания доступных пользователю видео по ссылкам из Instagram и TikTok.

## Возможности

- Instagram Reels и видеопосты
- TikTok
- короткие `vm.tiktok.com` и `vt.tiktok.com` ссылки
- автоматическое определение платформы
- скачивание через `yt-dlp`
- объединение видео и аудио через FFmpeg
- ограничение параллельных загрузок
- автоматическая очистка временных файлов
- Docker / Docker Compose
- опциональный `cookies.txt` для случаев, когда Instagram требует авторизацию

> Используйте бот только для публично доступных материалов и контента, который вы имеете право скачивать. Бот не предназначен для обхода DRM или ограничений доступа.

## Быстрый запуск в Windows

Требуется Python 3.11–3.13 и FFmpeg в `PATH`.

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
```

Откройте `.env` и укажите токен:

```env
BOT_TOKEN=ВАШ_ТОКЕН_ОТ_BOTFATHER
```

Запуск:

```powershell
python bot.py
```

## Docker

```bash
cp .env.example .env
# укажите BOT_TOKEN в .env
docker compose up -d --build
```

Логи:

```bash
docker compose logs -f bot
```

Остановка:

```bash
docker compose down
```

## Instagram cookies

Некоторые ссылки Instagram могут потребовать авторизацию. В этом случае экспортируйте cookies собственного аккаунта в Netscape-формате в `cookies.txt` и включите в `.env`:

```env
COOKIES_FILE=/app/secrets/cookies.txt
```

Затем раскомментируйте volume для `cookies.txt` в `docker-compose.yml`.

Не добавляйте `cookies.txt` или `.env` в Git — они уже исключены через `.gitignore`.

## Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---:|---|
| `BOT_TOKEN` | — | токен Telegram-бота |
| `DOWNLOAD_DIR` | `downloads` | каталог временных файлов |
| `MAX_UPLOAD_MB` | `49` | максимальный размер отправляемого файла |
| `MAX_CONCURRENT_DOWNLOADS` | `3` | одновременно выполняемых скачиваний |
| `COOKIES_FILE` | пусто | путь к Netscape cookies |
| `FFMPEG_LOCATION` | пусто | путь к FFmpeg, если он не в `PATH` |

## Структура

```text
VideoDownloaderBot/
├── bot.py
├── config.py
├── downloader.py
├── requirements.txt
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── README.md
└── tests/
    └── test_urls.py
```
