from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config import Settings
from downloader import (
    DownloadError,
    FileTooLargeError,
    UnsupportedUrlError,
    download_video,
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
    "🎬 Отправьте ссылку на видео из Instagram или TikTok.\n\n"
    "Поддерживаются:\n"
    "• Instagram Reels / видеопосты\n"
    "• TikTok и короткие vm.tiktok.com / vt.tiktok.com ссылки\n\n"
    "Бот предназначен для публично доступных видео и контента, который вы имеете право скачивать."
)


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
        await message.reply_text("Пришлите ссылку на Instagram или TikTok.")
        return

    status = await message.reply_text(f"⏳ Скачиваю видео из {platform}…")

    try:
        async with DOWNLOAD_SEMAPHORE:
            await context.bot.send_chat_action(
                chat_id=message.chat_id,
                action=ChatAction.UPLOAD_VIDEO,
            )
            result = await asyncio.to_thread(
                download_video,
                url,
                platform,
                SETTINGS.download_dir,
                SETTINGS.max_upload_mb,
                SETTINGS.cookies_file,
                SETTINGS.ffmpeg_location,
            )

        try:
            caption_lines = [f"✅ {result.platform}"]
            if result.source != "yt-dlp":
                logger.info("Downloaded via fallback source: %s", result.source)
            if result.author:
                caption_lines.append(f"👤 {result.author}")
            caption = "\n".join(caption_lines)

            with result.path.open("rb") as video_file:
                if result.path.suffix.lower() == ".mp4":
                    await message.reply_video(
                        video=video_file,
                        caption=caption,
                        supports_streaming=True,
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

    except FileTooLargeError as exc:
        logger.info("Video too large: %.1f MB", exc.size_mb)
        await status.edit_text(
            f"⚠️ Видео весит {exc.size_mb:.1f} МБ и превышает установленный лимит "
            f"{exc.limit_mb} МБ."
        )
    except DownloadError as exc:
        logger.warning("Download failed for %s: %s", url, exc)
        await status.edit_text(
            "❌ Не удалось скачать видео. Возможно, публикация приватная, удалена, "
            "требует авторизации или Instagram/TikTok изменил способ выдачи видео."
        )
    except Exception:
        logger.exception("Unexpected error while processing %s", url)
        await status.edit_text("❌ Произошла внутренняя ошибка. Попробуйте другую ссылку.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Telegram update failed", exc_info=context.error)


def main() -> None:
    app = Application.builder().token(SETTINGS.bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)

    logger.info("VideoDownloaderBot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
