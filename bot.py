from __future__ import annotations

import asyncio
import logging
import secrets
import time
import threading
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from config import Settings
from yandex_disk import DiskError, upload_file, UPLOAD_TIMEOUT
from russian import download_russian
from downloader import (
    DownloadError,
    NoMediaFileError,
    FileTooLargeError,
    UnsupportedUrlError,
    download_video,
    download_youtube_original,
    download_audio,
    extract_supported_url,
)
from instagram_original import download_instagram_original
from user_registry import init_users_db, record_user, users_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("video_downloader_bot")

SETTINGS = Settings.from_env()
USERS_DB = SETTINGS.download_dir / "users.sqlite3"
init_users_db(USERS_DB)
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(SETTINGS.max_concurrent_downloads)
DISK_SEMAPHORE = asyncio.Semaphore(1)

WELCOME = (
    "🎬 Отправьте ссылку на ролик из Instagram, TikTok, YouTube или Pinterest.\n\n"
    "Поддерживаются YouTube Shorts и короткие ссылки youtu.be / pin.it.\n"
    "После ссылки выберите: 🎬 Видео или 🎵 MP3.\n"
    "Instagram: дополнительно доступен 📦 Оригинал — файл без обработки Telegram-плеером.\n"
    "Для YouTube также доступны русская аудиодорожка и субтитры.\n"
    "Pinterest: фото и видеопины.\n\n"
    "Большие файлы отправляются ссылкой на Яндекс Диск, если он подключён администратором.\n"
    "Скачивайте материалы, которые вы имеете право сохранять."
)
CHOICES: dict[str, tuple[int, int, str, str, float]] = {}
ACTIVE_USERS: set[int] = set()


def remember_choice(user_id: int, chat_id: int, url: str, platform: str) -> str:
    now = time.monotonic()
    for key, value in list(CHOICES.items()):
        if now - value[4] > 3600:
            CHOICES.pop(key, None)
    while len(CHOICES) >= 1000:
        CHOICES.pop(next(iter(CHOICES)))
    key = secrets.token_hex(8)
    CHOICES[key] = (user_id, chat_id, url, platform, now)
    return key


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    record_user(USERS_DB, update.effective_user)
    if update.effective_message:
        await update.effective_message.reply_text(WELCOME)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    record_user(USERS_DB, update.effective_user)
    if update.effective_message:
        await update.effective_message.reply_text(WELCOME)


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    record_user(USERS_DB, user)

    if SETTINGS.admin_id is None:
        await message.reply_text(
            "⚙️ Команда /users отключена: добавьте ADMIN_ID в .env."
        )
        return

    if user.id != SETTINGS.admin_id:
        await message.reply_text("⛔ Нет доступа.")
        return

    total_users, total_requests, rows = users_summary(USERS_DB, limit=100)
    header = (
        f"👥 Пользователи бота: {total_users}\n"
        f"📥 Скачиваний запрошено: {total_requests}\n"
        f"Показаны последние: {len(rows)}\n\n"
    )

    lines = []
    for index, row in enumerate(rows, 1):
        username = f"@{row['username']}" if row.get("username") else "без username"
        full_name = " ".join(
            part for part in (row.get("first_name"), row.get("last_name")) if part
        ).strip() or "без имени"
        full_name = full_name.replace("\n", " ")[:60]
        last_seen = str(row.get("last_seen") or "").replace("T", " ")[:16]
        lines.append(
            f"{index}. {username} | {full_name}\n"
            f"ID: {row['user_id']} | 📥 {row['request_count']} | 🕒 {last_seen} UTC"
        )

    if not lines:
        await message.reply_text(header + "Пока пользователей нет.")
        return

    chunks = []
    current = header
    for line in lines:
        block = line + "\n\n"
        if len(current) + len(block) > 3800:
            chunks.append(current.rstrip())
            current = block
        else:
            current += block
    if current.strip():
        chunks.append(current.rstrip())

    for chunk in chunks:
        await message.reply_text(chunk)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return

    try:
        url, platform = extract_supported_url(message.text)
    except UnsupportedUrlError:
        record_user(USERS_DB, update.effective_user)
        await message.reply_text("Пришлите ссылку на Instagram, TikTok, YouTube или Pinterest.")
        return

    record_user(USERS_DB, update.effective_user, increment_requests=True)
    key = remember_choice(update.effective_user.id, message.chat_id, url, platform)

    rows = [[
        InlineKeyboardButton(
            "🖼 Фото / видео" if platform == "Pinterest" else "🎬 Видео",
            callback_data=f"download:video:{key}",
        ),
        InlineKeyboardButton("🎵 MP3", callback_data=f"download:audio:{key}"),
    ]]

    if platform in {"Instagram", "YouTube"}:
        rows.append([
            InlineKeyboardButton(
                "📦 Оригинал",
                callback_data=f"download:original:{key}",
            )
        ])

    if platform == "YouTube":
        rows.append([
            InlineKeyboardButton("🇷🇺 Русская дорожка", callback_data=f"download:ruvideo:{key}"),
            InlineKeyboardButton("📝 Русские субтитры", callback_data=f"download:rusubs:{key}"),
        ])

    await message.reply_text(
        f"{platform}: что скачать?",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def handle_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.message is None:
        return
    _, mode, key = query.data.split(":", 2)
    if mode == "fit":
        await query.answer("Сжатие отключено. Пришлите ссылку заново: большие файлы отправляются на Яндекс Диск.", show_alert=True)
        return
    choice = CHOICES.get(key)
    if choice is None or time.monotonic() - choice[4] > 3600:
        await query.answer("Кнопка устарела. Пришлите ссылку ещё раз.", show_alert=True)
        return
    user_id, chat_id, url, platform, _ = choice
    if query.from_user.id != user_id or query.message.chat_id != chat_id:
        await query.answer("Эти кнопки для отправителя ссылки.", show_alert=True)
        return
    if user_id in ACTIVE_USERS:
        await query.answer("Дождитесь завершения текущей загрузки.", show_alert=True)
        return
    ACTIVE_USERS.add(user_id)
    try:
        await query.answer()
        await send_download(query.message, context, url, platform, mode)
    finally:
        ACTIVE_USERS.discard(user_id)


async def _download(url: str, platform: str, mode: str):
    async with DOWNLOAD_SEMAPHORE:
        if platform == "YouTube" and (mode == "original" or (mode == "video" and SETTINGS.yandex_disk_token)):
            return await asyncio.to_thread(
                download_youtube_original, url, SETTINGS.download_dir, SETTINGS.download_limit_mb,
                SETTINGS.cookies_file, SETTINGS.ffmpeg_location,
                telegram_limit_mb=SETTINGS.max_upload_mb if mode == "video" else None,
            )
        if mode in {"ruvideo", "rusubs"}:
            return await asyncio.to_thread(
                download_russian, url, platform, SETTINGS.download_dir, SETTINGS.download_limit_mb,
                SETTINGS.cookies_file, SETTINGS.ffmpeg_location, subtitles=mode == "rusubs",
                telegram_limit_mb=SETTINGS.max_upload_mb if SETTINGS.yandex_disk_token else None,
            )

        if platform == "Instagram" and mode in {"video", "original"}:
            try:
                return await asyncio.to_thread(
                    download_instagram_original,
                    url,
                    SETTINGS.download_dir,
                    SETTINGS.download_limit_mb,
                    SETTINGS.cookies_file,
                    SETTINGS.ffmpeg_location,
                )
            except FileTooLargeError:
                raise
            except Exception as exc:
                if mode == "original":
                    raise
                logger.warning(
                    "Instagram native stream failed; falling back to regular downloader: %s",
                    exc,
                )

        return await asyncio.to_thread(
            download_audio if mode == "audio" else download_video,
            url, platform, SETTINGS.download_dir, SETTINGS.download_limit_mb,
            SETTINGS.cookies_file, SETTINGS.ffmpeg_location,
        )


def _cleanup_late_download(task):
    if not task.cancelled():
        try:
            task.result().cleanup()
        except Exception:
            pass


async def wait_for_disk(task, status, state, cancel, timeout=UPLOAD_TIMEOUT):
    """Bound the whole operation, including queueing; keep worker cleanup separate."""
    deadline = time.monotonic() + timeout
    last_text = None
    try:
        while not task.done():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DiskError("Загрузка на Яндекс Диск не завершилась за 10 минут. Попробуйте позже.")
            done, _ = await asyncio.wait({task}, timeout=min(10, remaining))
            if done:
                break
            stage, sent, total = state[0]
            text = f"☁️ Яндекс Диск: {stage}."
            if stage == "отправка файла" and total:
                text += f"\nОтправлено {sent / total:.0%}: {sent / 1024 / 1024:.1f} из {total / 1024 / 1024:.1f} МБ."
            if text != last_text:
                try:
                    await asyncio.wait_for(status.edit_text(text), timeout=5)
                except Exception:
                    logger.warning("Could not update Disk progress message")
                last_text = text
            logger.info("Yandex Disk progress: stage=%s; sent=%s; total=%s", stage, sent, total)
        return task.result()
    except BaseException:
        cancel.set()
        raise


async def send_download(message, context, url: str, platform: str, mode: str) -> None:
    status = await message.reply_text("⏳ В очереди на скачивание…")
    task = None
    upload_task = None

    try:
        label = {
            "audio": "MP3",
            "original": "оригинальный файл",
            "ruvideo": "видео с русской дорожкой",
            "rusubs": "русские субтитры",
        }.get(mode, "видео")
        await status.edit_text(f"⏳ Готовлю {label} из {platform}…")
        task = asyncio.create_task(_download(url, platform, mode))
        result = await asyncio.wait_for(asyncio.shield(task), timeout=900 if SETTINGS.yandex_disk_token else 300)

        try:
            if result.path.stat().st_size > SETTINGS.max_upload_mb * 1024 * 1024:
                if not SETTINGS.yandex_disk_token:
                    raise DiskError("Файл превышает лимит Telegram. Администратору нужно подключить Яндекс Диск: YANDEX_DISK_TOKEN.")
                await status.edit_text("☁️ Файл больше лимита Telegram. Ожидаю отправку на Яндекс Диск…")
                state = [("ожидание очереди", 0, result.path.stat().st_size)]
                cancel = threading.Event()
                def progress(stage, sent, total):
                    state[0] = (stage, sent, total)
                async def transfer():
                    async with DISK_SEMAPHORE:
                        if cancel.is_set():
                            raise DiskError("Ожидание отправки на Диск отменено.")
                        return await asyncio.to_thread(upload_file, result.path, SETTINGS.yandex_disk_token,
                                                       SETTINGS.yandex_disk_folder, progress=progress, cancel=cancel)
                upload_task = asyncio.create_task(transfer())
                public_url = await wait_for_disk(upload_task, status, state, cancel)
                await message.reply_text(
                    f"✅ {result.platform} — {result.path.stat().st_size / 1024 / 1024:.1f} МБ\n"
                    f"{result.title}\nФайл на Яндекс Диске. Ссылка доступна всем, у кого она есть.\n{public_url}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("☁️ Скачать с Яндекс Диска", url=public_url)]]),
                    disable_web_page_preview=True,
                )
                await status.delete()
                return
            caption_lines = [f"✅ {result.platform}"]
            if mode == "original":
                caption_lines.append("📦 Оригинальный файл без обработки Telegram-плеером")
            elif mode == "ruvideo":
                caption_lines.append("🇷🇺 Русская аудиодорожка YouTube")
            elif mode == "rusubs":
                caption_lines.append("📝 Русские субтитры — отдельный файл")
                if result.source == "russian-auto-captions":
                    caption_lines.append("Автоматические субтитры YouTube")
            if result.source != "yt-dlp":
                logger.info("Downloaded via fallback source: %s", result.source)
            if result.author:
                caption_lines.append(f"👤 {result.author}")
            caption = "\n".join(caption_lines)

            with result.path.open("rb") as video_file:
                if mode == "audio":
                    await message.reply_audio(
                        audio=video_file, title=result.title, performer=result.author,
                        caption=caption, read_timeout=180, write_timeout=180,
                        connect_timeout=30, pool_timeout=30,
                    )
                elif mode == "original":
                    logger.info(
                        "Sending original as document: %s (%sx%s)",
                        result.path.name,
                        result.width,
                        result.height,
                    )
                    await message.reply_document(
                        document=video_file,
                        filename=f"{result.platform.lower()}_original{result.path.suffix or '.mp4'}",
                        caption=caption,
                        disable_content_type_detection=True,
                        read_timeout=180,
                        write_timeout=180,
                        connect_timeout=30,
                        pool_timeout=30,
                    )
                elif result.source == "pinterest-photo":
                    try:
                        await message.reply_photo(photo=video_file, caption=caption,
                                                  read_timeout=180, write_timeout=180)
                    except BadRequest:
                        video_file.seek(0)
                        await message.reply_document(document=video_file, caption=caption,
                                                     read_timeout=180, write_timeout=180)
                elif result.path.suffix.lower() == ".mp4":
                    video_kwargs = {}
                    if (
                        (result.source.startswith("instagram-original") or result.platform == "YouTube")
                        and result.width
                        and result.height
                    ):
                        video_kwargs["width"] = result.width
                        video_kwargs["height"] = result.height
                        logger.info(
                            "Sending video with explicit Telegram dimensions: %sx%s",
                            result.width,
                            result.height,
                        )
                    await message.reply_video(
                        video=video_file,
                        caption=caption,
                        supports_streaming=True,
                        read_timeout=180,
                        write_timeout=180,
                        connect_timeout=30,
                        pool_timeout=30,
                        **video_kwargs,
                    )
                else:
                    await message.reply_document(
                        document=video_file,
                        caption=caption,
                        read_timeout=180,
                        write_timeout=180,
                        connect_timeout=30,
                        pool_timeout=30,
                    )
            await status.delete()
        finally:
            if upload_task is not None and not upload_task.done():
                def finish_upload(completed):
                    if not completed.cancelled():
                        completed.exception()  # Consume a late error without logging signed URLs.
                    result.cleanup()
                upload_task.add_done_callback(finish_upload)
            else:
                result.cleanup()

    except asyncio.CancelledError:
        if task is not None and not task.done():
            task.add_done_callback(_cleanup_late_download)
        raise
    except asyncio.TimeoutError:
        if task is not None:
            task.add_done_callback(_cleanup_late_download)
        logger.warning("Download timed out for %s", url)
        await status.edit_text("⏱ Обработка не завершилась за отведённое время. Попробуйте более короткий ролик.")
    except FileTooLargeError as exc:
        logger.info("Video too large: %.1f MB", exc.size_mb)
        await status.edit_text(
            f"⚠️ Файл весит {exc.size_mb:.1f} МБ и превышает установленный лимит "
            f"{exc.limit_mb} МБ."
            + (" Это предельный размер загрузки на сервер; сжатие отключено." if SETTINGS.yandex_disk_token
               else " Для больших файлов администратору нужно подключить Яндекс Диск: YANDEX_DISK_TOKEN.")
        )
    except DiskError as exc:
        logger.warning("Yandex Disk delivery failed: %s", exc)
        await status.edit_text(f"☁️ {exc}")
    except NoMediaFileError as exc:
        await status.edit_text(f"❌ {exc}")
    except DownloadError as exc:
        logger.warning("Download failed for %s: %s", url, exc)
        await status.edit_text(
            "❌ Не удалось получить файл с сайта. Публикация может быть недоступна "
            "или сайт ограничил загрузку. Подробная причина записана в логах бота."
        )
    except Exception:
        logger.exception("Unexpected error while processing %s", url)
        await status.edit_text("❌ Произошла внутренняя ошибка. Попробуйте другую ссылку.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Telegram update failed", exc_info=context.error)


def main() -> None:
    app = (
        Application.builder()
        .token(SETTINGS.bot_token)
        .concurrent_updates(True)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CallbackQueryHandler(handle_choice, pattern=r"^download:(video|audio|original|fit|ruvideo|rusubs):[0-9a-f]{16}$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)

    version = Path(__file__).with_name("VERSION").read_text().strip()
    logger.info("VideoDownloaderBot v%s started; Yandex Disk configured=%s", version, bool(SETTINGS.yandex_disk_token))
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
