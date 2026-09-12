from __future__ import annotations

import logging
import shutil
import subprocess
import uuid
from pathlib import Path

import yt_dlp

from downloader import (
    DEFAULT_HEADERS,
    DownloadError,
    DownloadResult,
    FileTooLargeError,
    _check_size,
    _ffmpeg_binary,
    _pick_output_file,
)
from media_runtime import resolve_ffmpeg

logger = logging.getLogger("video_downloader_bot.instagram_original")


def _prepare_native_for_telegram(
    source: Path,
    info: dict,
    work_dir: Path,
    max_upload_mb: int,
    ffmpeg_location: str | None,
) -> Path:
    """Keep Instagram's selected frame geometry; only fix codec/container if needed."""
    vcodec = str(info.get("vcodec") or "").lower()
    acodec = str(info.get("acodec") or "").lower()

    video_is_h264 = vcodec.startswith("h264") or vcodec.startswith("avc1")
    audio_is_aac = (
        not acodec
        or acodec == "none"
        or acodec.startswith("aac")
        or acodec.startswith("mp4a")
    )

    # If Instagram already supplied a Telegram-friendly MP4, do not touch it.
    if source.suffix.lower() == ".mp4" and video_is_h264 and audio_is_aac:
        logger.info(
            "Instagram native MP4 kept unchanged: %sx%s codec=%s/%s",
            info.get("width"),
            info.get("height"),
            vcodec or "unknown",
            acodec or "unknown",
        )
        return source

    binary = _ffmpeg_binary(ffmpeg_location)
    target = work_dir / "instagram_native.telegram.mp4"

    args = [
        binary,
        "-nostdin",
        "-y",
        "-v",
        "error",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
    ]

    # Never scale, crop or change SAR here. We keep the selected Instagram
    # rendition's frame geometry exactly as delivered by Instagram.
    if video_is_h264:
        args += ["-c:v", "copy"]
    else:
        args += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
        ]

    if not acodec or acodec == "none":
        pass
    elif audio_is_aac:
        args += ["-c:a", "copy"]
    else:
        args += ["-c:a", "aac", "-b:a", "128k"]

    args += ["-movflags", "+faststart", str(target)]

    logger.info(
        "Instagram native stream packaging: %sx%s aspect=%s video=%s audio=%s",
        info.get("width"),
        info.get("height"),
        info.get("aspect_ratio"),
        "copy" if video_is_h264 else "h264 convert",
        "none" if not acodec or acodec == "none" else ("copy" if audio_is_aac else "aac convert"),
    )

    try:
        subprocess.run(args, check=True, capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DownloadError(
            "Не удалось упаковать исходный поток Instagram в MP4."
        ) from exc

    _check_size(target, max_upload_mb)
    return target


def _download_once(
    url: str,
    work_dir: Path,
    max_upload_mb: int,
    cookies_file: Path | None,
    ffmpeg_location: str | None,
    source: str,
) -> DownloadResult:
    """Download Instagram's best native reel rendition without resizing/cropping."""
    opts: dict = {
        # Reels often expose both square/progressive MP4 and portrait DASH
        # renditions. Prefer a portrait rendition when Instagram provides one,
        # then fall back to the best native stream of any aspect ratio.
        "format": "bv*[aspect_ratio<1]+ba/b[aspect_ratio<1]/bv*+ba/b",
        # DASH may use VP9/other codecs. Merge losslessly first; below we only
        # convert codecs/container when Telegram requires it, never geometry.
        "merge_output_format": "mkv",
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

    logger.info(
        "Instagram native format selected: id=%s %sx%s aspect=%s vcodec=%s acodec=%s file=%s",
        info.get("format_id"),
        info.get("width"),
        info.get("height"),
        info.get("aspect_ratio"),
        info.get("vcodec"),
        info.get("acodec"),
        output.name,
    )

    output = _prepare_native_for_telegram(
        output,
        info,
        work_dir,
        max_upload_mb,
        ffmpeg_location,
    )
    size_bytes = _check_size(output, max_upload_mb)

    width = int(info["width"]) if info.get("width") else None
    height = int(info["height"]) if info.get("height") else None

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
    """Get Instagram's own reel rendition while preserving its native geometry."""
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
