from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

import yt_dlp

from downloader import (
    DEFAULT_HEADERS,
    DownloadError,
    DownloadResult,
    FileTooLargeError,
    _check_size,
    _pick_output_file,
)
from media_runtime import resolve_ffmpeg

logger = logging.getLogger("video_downloader_bot.instagram_original")


def _download_once(
    url: str,
    work_dir: Path,
    max_upload_mb: int,
    cookies_file: Path | None,
    ffmpeg_location: str | None,
    source: str,
) -> DownloadResult:
    """Download Instagram's best native video stream without resizing/re-encoding."""
    opts: dict = {
        # Prefer the highest quality native MP4 video + M4A audio streams.
        # FFmpeg only muxes them together; it does not scale or re-encode video.
        "format": "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b[ext=mp4]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(work_dir / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "restrictfilenames": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "overwrites": True,
        "http_headers": DEFAULT_HEADERS,
        "max_filesize": max_upload_mb * 1024 * 1024,
        "playlistend": 1,
    }

    effective_ffmpeg = ffmpeg_location or resolve_ffmpeg()
    if effective_ffmpeg:
        opts["ffmpeg_location"] = effective_ffmpeg
    if cookies_file is not None:
        opts["cookiefile"] = str(cookies_file)

    logger.info(
        "Instagram original stream download: cookies=%s",
        "yes" if cookies_file is not None else "no",
    )

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    if not isinstance(info, dict):
        raise DownloadError("Instagram не вернул данные видео.")

    output = _pick_output_file(work_dir, info)
    size_bytes = _check_size(output, max_upload_mb)

    width = int(info["width"]) if info.get("width") else None
    height = int(info["height"]) if info.get("height") else None
    logger.info(
        "Instagram native stream selected: %sx%s file=%s",
        width,
        height,
        output.name,
    )

    title = str(info.get("title") or info.get("description") or "Instagram video")
    author = info.get("uploader") or info.get("creator") or info.get("channel")

    return DownloadResult(
        path=output,
        title=title[:200],
        author=str(author)[:100] if author else None,
        platform="Instagram",
        webpage_url=str(info.get("webpage_url") or url),
        size_bytes=size_bytes,
        work_dir=work_dir,
        width=width,
        height=height,
        source=source,
    )


def download_instagram_original(
    url: str,
    download_root: Path,
    max_upload_mb: int,
    cookies_file: Path | None = None,
    ffmpeg_location: str | None = None,
) -> DownloadResult:
    """Get the native Instagram rendition, keeping its original frame geometry."""
    work_dir = download_root / uuid.uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=False)

    public_error: Exception | None = None
    try:
        try:
            return _download_once(
                url,
                work_dir,
                max_upload_mb,
                None,
                ffmpeg_location,
                "instagram-original-public",
            )
        except FileTooLargeError:
            raise
        except Exception as exc:
            public_error = exc
            logger.warning("Instagram original public attempt failed: %s", exc)

        if cookies_file is not None:
            for item in work_dir.iterdir():
                if item.is_file() or item.is_symlink():
                    try:
                        item.unlink()
                    except OSError:
                        pass
                elif item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)

            try:
                return _download_once(
                    url,
                    work_dir,
                    max_upload_mb,
                    cookies_file,
                    ffmpeg_location,
                    "instagram-original-session",
                )
            except FileTooLargeError:
                raise
            except Exception as exc:
                logger.warning("Instagram original session attempt failed: %s", exc)
                raise DownloadError(
                    f"Не удалось получить исходный поток Instagram: {exc}"
                ) from exc

        raise DownloadError(
            f"Не удалось получить исходный поток Instagram: {public_error}"
        )
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
