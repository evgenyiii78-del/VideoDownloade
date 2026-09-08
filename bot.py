from __future__ import annotations

import asyncio
import logging
import secrets
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from config import Settings
from russian import download_russian
from downloader import (
    DownloadError,
    NoMediaFileError,
    FileTooLargeError,
    UnsupportedUrlError,
    download_video,
    download_audio,
    extract_supported_url,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("video_downloader_bot")

SETTINGS = Settings.from_env()
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(SETTINGS.max_concurrent_downloads)

WELCOME = (
    "🎬 Отправьте ссылку на ролик из Instagram, TikTok, YouTube или Pinterest.\n\n"
    "Поддерживаются YouTube Shorts и короткие ссылки youtu.be / pin.it.\n"
    "После ссылки выберите: 🎬 Видео или 🎵 MP3.\n"
    "Для YouTube также доступны русская аудиодорожка и субтитры.\n"
    "Pinterest: фото и видеопины.\n\n"
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
    if update.effective_message:
        await update.effective_message.reply_text(WELCOME)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(WELCOME)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return

    try:
        url, platform = extract_supported_url(message.text)
    except UnsupportedUrlError:
        await message.reply_text("Пришлите ссылку на Instagram, TikTok, YouTube или Pinterest.")
        return

    key = remember_choice(update.effective_user.id, message.chat_id, url, platform)
    await message.reply_text(
        f"{platform}: что скачать?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🖼 Фото / видео" if platform == "Pinterest" else "🎬 Видео", callback_data=f"download:video:{key}"),
            InlineKeyboardButton("🎵 MP3", callback_data=f"download:audio:{key}"),
        ]] + ([[
            InlineKeyboardButton("🇷🇺 Русская дорожка", callback_data=f"download:ruvideo:{key}"),
            InlineKeyboardButton("📝 Русские субтитры", callback_data=f"download:rusubs:{key}"),
        ]] if platform == "YouTube" else [])),
    )


async def handle_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.message is None:
        return
    _, mode, key = query.data.split(":", 2)
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
        if mode in {"ruvideo", "rusubs"}:
            return await asyncio.to_thread(
                download_russian, url, platform, SETTINGS.download_dir, SETTINGS.max_upload_mb,
                SETTINGS.cookies_file, SETTINGS.ffmpeg_location, subtitles=mode == "rusubs",
            )
        return await asyncio.to_thread(
            download_audio if mode == "audio" else download_video,
            url, platform, SETTINGS.download_dir, SETTINGS.max_upload_mb,
            SETTINGS.cookies_file, SETTINGS.ffmpeg_location,
        )


def _cleanup_late_download(task):
    if not task.cancelled():
        try:
            task.result().cleanup()
        except Exception:
            pass


async def send_download(message, context, url: str, platform: str, mode: str) -> None:
    status = await message.reply_text("⏳ В очереди на скачивание…")
    task = None

    try:
        label = {"audio": "MP3", "ruvideo": "видео с русской дорожкой", "rusubs": "русские субтитры"}.get(mode, "видео")
        await status.edit_text(f"⏳ Готовлю {label} из {platform}…")
        task = asyncio.create_task(_download(url, platform, mode))
        result = await asyncio.wait_for(asyncio.shield(task), timeout=300)

        try:
            caption_lines = [f"✅ {result.platform}"]
            if mode == "ruvideo":
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
                elif result.source == "pinterest-photo":
                    try:
                        await message.reply_photo(photo=video_file, caption=caption,
                                                  read_timeout=180, write_timeout=180)
                    except BadRequest:
                        video_file.seek(0)
                        await message.reply_document(document=video_file, caption=caption,
                                                     read_timeout=180, write_timeout=180)
                elif result.path.suffix.lower() == ".mp4":
                    await message.reply_video(
                        video=video_file,
                        caption=caption,
                        supports_streaming=True,
                        width=result.width,
                        height=result.height,
                        read_timeout=180,
                        write_timeout=180,
                        connect_timeout=30,
                        pool_timeout=30,
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
            result.cleanup()

    except asyncio.TimeoutError:
        if task is not None:
            task.add_done_callback(_cleanup_late_download)
        logger.warning("Download timed out for %s", url)
        await status.edit_text("⏱ Не удалось скачать файл за 5 минут. Попробуйте ещё раз или другую ссылку.")
    except FileTooLargeError as exc:
        logger.info("Video too large: %.1f MB", exc.size_mb)
        await status.edit_text(
            f"⚠️ Файл весит {exc.size_mb:.1f} МБ и превышает установленный лимит "
            f"{exc.limit_mb} МБ."
        )
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
    app.add_handler(CallbackQueryHandler(handle_choice, pattern=r"^download:(video|audio|ruvideo|rusubs):[0-9a-f]{16}$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)

    logger.info("VideoDownloaderBot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
