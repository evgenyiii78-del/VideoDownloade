# VideoDownloaderBot v0.4.0

Telegram-бот для скачивания доступных пользователю видео по ссылкам из Instagram, TikTok, YouTube и Pinterest.

## Новое в v0.4.0

- YouTube: обычные ролики, Shorts и ссылки `youtu.be`.
- Pinterest: фото и видеопины, в том числе короткие ссылки `pin.it`. Доски целиком не поддерживаются. Фотопины отправляются изображением, а при ограничениях Telegram — файлом.
- Выбор «Видео» / «MP3» (для Pinterest — «Фото / видео» / «MP3») после отправки ссылки. Кнопки работают 1 час и доступны отправителю ссылки.
- MP3 192 кбит/с отправляется как аудиофайл с названием. В ролике должен быть звук.
- Лимит размера применяется и к MP3. Для Instagram сначала загружается видео, поэтому оно тоже должно укладываться в лимит.

### Обновление на Bothost

Обновите код из GitHub, переустановите зависимости из `requirements.txt` и перезапустите бота.
При Docker-развёртывании пересоберите образ: он уже содержит FFmpeg и Node.js 22.
При обычном Python-развёртывании нужны FFmpeg и Node.js 22+ (или Deno 2+), доступные в PATH.
Токен и настройки Instagram в `.env` сохраняются.

YouTube использует `yt-dlp[default]` и JavaScript runtime по [документации yt-dlp](https://github.com/yt-dlp/yt-dlp#dependencies).
Сайт может ограничивать загрузку с IP сервера; поддержку конкретной ссылки нужно проверять на хостинге.

## Возможности

- Instagram Reels и видеопосты
- TikTok
- короткие `vm.tiktok.com` и `vt.tiktok.com` ссылки
- автоматическое определение платформы
- `yt-dlp` + FFmpeg
- автоматический Instagram fallback через `vxinstagram.com` / Open Graph\n- Instagram-сессия через `.env` или `cookies.txt` как дополнительный вариант
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


## Как работает Instagram в v0.4.0

1. Бот принимает обычную ссылку вида `https://www.instagram.com/reel/HASH/`.
2. Сначала пробует публичную загрузку без авторизации.
3. Если Instagram блокирует публичную загрузку, бот автоматически повторяет запрос через служебную Instagram-сессию, сохранённую на сервере.
4. Если служебная сессия тоже не сработала, используется best-effort публичный fallback.
5. MP4 отправляется пользователю в Telegram.

Пользователю не нужно входить в Instagram, передавать cookies или менять ссылку. Служебную сессию один раз настраивает администратор бота в переменных окружения Bothost.
