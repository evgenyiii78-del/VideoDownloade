"""Explicit size-limited encoding; keep the source frame geometry."""
import re
import logging
import subprocess
from dataclasses import replace

from downloader import (NoMediaFileError, _ffmpeg_binary, _check_size,
                        _video_display_dimensions, download_youtube_original)


logger = logging.getLogger("video_downloader_bot.youtube_fit")

def fit_video(result, limit_mb, ffmpeg=None):
    binary = _ffmpeg_binary(ffmpeg)
    probe = subprocess.run([binary, '-nostdin', '-hide_banner', '-i', str(result.path)],
                           capture_output=True, text=True, timeout=30)
    match = re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)', probe.stderr)
    if not match:
        raise NoMediaFileError('Не удалось определить длительность для сжатия.')
    hours, minutes, seconds = map(float, match.groups())
    duration = hours * 3600 + minutes * 60 + seconds
    if duration <= 0:
        raise NoMediaFileError('Не удалось определить длительность для сжатия.')
    # Reserve 8% for container overhead and encoder variability.
    audio_rate = 96000
    video_rate = int(limit_mb * 1024 * 1024 * 8 * 0.92 / duration) - audio_rate
    if video_rate < 32000:
        raise NoMediaFileError('Ролик слишком длинный для этого лимита даже со сжатием.')
    target = result.work_dir / 'youtube_fit.mp4'
    passlog = str(result.work_dir / 'encode_pass')
    common = [binary, '-nostdin', '-y', '-v', 'error', '-i', str(result.path),
              '-map', '0:v:0', '-c:v', 'libx264', '-preset', 'fast',
              '-pix_fmt', 'yuv420p', '-b:v', str(video_rate), '-passlogfile', passlog]
    try:
        import os
        subprocess.run(common + ['-pass', '1', '-an', '-f', 'null', os.devnull],
                       check=True, capture_output=True, timeout=360)
        subprocess.run(common + ['-pass', '2', '-map', '0:a:0', '-c:a', 'aac',
                                  '-b:a', str(audio_rate), '-sn', '-dn', '-movflags',
                                  '+faststart', str(target)],
                       check=True, capture_output=True, timeout=360)
    except subprocess.TimeoutExpired as exc:
        logger.warning("FFmpeg size encoding timed out after %s seconds", exc.timeout)
        raise NoMediaFileError('Сжатие превысило время обработки на сервере.') from exc
    except subprocess.CalledProcessError as exc:
        details = exc.stderr or b''
        if isinstance(details, bytes):
            details = details.decode('utf-8', errors='replace')
        logger.error("FFmpeg size encoding failed: exit=%s; stderr=%s", exc.returncode, details[-6000:])
        raise NoMediaFileError('FFmpeg завершился с ошибкой обработки. Причина записана в логах бота: FFmpeg size encoding failed.') from exc
    except OSError as exc:
        logger.error("Unable to run FFmpeg: %s", exc)
        raise NoMediaFileError('Не удалось запустить FFmpeg на сервере. Причина записана в логах бота.') from exc
    size = _check_size(target, limit_mb)
    width, height = _video_display_dimensions(target, ffmpeg)
    return replace(result, path=target, size_bytes=size, width=width, height=height,
                   source='youtube-fit')


def download_youtube_fit(url, root, limit_mb, cookies=None, ffmpeg=None):
    # Source download and Telegram output have separate limits.
    result = download_youtube_original(url, root, max(512, limit_mb), cookies, ffmpeg)
    try:
        return fit_video(result, limit_mb, ffmpeg)
    except Exception:
        result.cleanup()
        raise
