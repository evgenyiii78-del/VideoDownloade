from __future__ import annotations

import html
import re
import shutil
import uuid
import subprocess
import json
import os
from dataclasses import replace
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, urlunparse, urljoin

import httpx
import yt_dlp
from media_runtime import resolve_ffmpeg, youtube_js_runtimes
import logging

logger = logging.getLogger("video_downloader_bot.downloader")

SUPPORTED_HOSTS = {
    "youtube.com": "YouTube",
    "www.youtube.com": "YouTube",
    "m.youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "pinterest.com": "Pinterest",
    "www.pinterest.com": "Pinterest",
    "ru.pinterest.com": "Pinterest",
    "pinterest.ru": "Pinterest",
    "www.pinterest.ru": "Pinterest",
    "pin.it": "Pinterest",
    "de.pinterest.com": "Pinterest",
    "pinterest.de": "Pinterest",
    "www.pinterest.de": "Pinterest",
    "instagram.com": "Instagram",
    "www.instagram.com": "Instagram",
    "m.instagram.com": "Instagram",
    "tiktok.com": "TikTok",
    "www.tiktok.com": "TikTok",
    "m.tiktok.com": "TikTok",
    "vm.tiktok.com": "TikTok",
    "vt.tiktok.com": "TikTok",
}

URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 "
        "Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

INSTAGRAM_PROXY_HOSTS = (
    "vxinstagram.com",
    "ddinstagram.com",
)

VIDEO_META_KEYS = {
    "og:video",
    "og:video:url",
    "og:video:secure_url",
    "twitter:player:stream",
    "twitter:player:stream:url",
}


class DownloadError(RuntimeError):
    pass


class UnsupportedUrlError(DownloadError):
    pass


class NoMediaFileError(DownloadError):
    pass


class _DownloadLogger:
    def __init__(self, limit_mb: int):
        self.limit_mb = limit_mb
        self.skipped_size = None

    def debug(self, message):
        # yt-dlp reports max_filesize skips as ordinary output, not an exception.
        if 'larger than max-filesize' in message:
            match = re.search(r"\((\d+) bytes", message)
            self.skipped_size = int(match.group(1)) if match else self.limit_mb * 1024 * 1024 + 1
        logger.debug('%s', message)

    def info(self, message):
        self.debug(message)

    def warning(self, message):
        logger.warning('%s', message)

    def error(self, message):
        logger.error('%s', message)


class FileTooLargeError(DownloadError):
    def __init__(self, size_mb: float, limit_mb: int) -> None:
        self.size_mb = size_mb
        self.limit_mb = limit_mb
        super().__init__(f"Downloaded file is {size_mb:.1f} MB; limit is {limit_mb} MB")


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    title: str
    author: str | None
    platform: str
    webpage_url: str
    size_bytes: int
    work_dir: Path
    width: int | None = None
    height: int | None = None
    source: str = "yt-dlp"

    def cleanup(self) -> None:
        shutil.rmtree(self.work_dir, ignore_errors=True)


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta":
            return
        values = {str(k).lower(): (v or "") for k, v in attrs}
        key = (values.get("property") or values.get("name") or "").lower()
        content = values.get("content", "")
        if key and content and key not in self.meta:
            self.meta[key] = content


def extract_supported_url(text: str) -> tuple[str, str]:
    for match in URL_RE.finditer(text or ""):
        raw_url = match.group(0).rstrip(".,;:!?)]}\"'")
        parsed = urlparse(raw_url)
        host = (parsed.hostname or "").lower().rstrip(".")
        platform = SUPPORTED_HOSTS.get(host)
        if platform:
            return raw_url, platform
    raise UnsupportedUrlError("No supported video URL found")


def build_instagram_proxy_urls(url: str) -> list[str]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
        return []

    path = parsed.path or "/"
    if not path.endswith("/"):
        path += "/"

    return [
        urlunparse(("https", proxy_host, path, "", "", ""))
        for proxy_host in INSTAGRAM_PROXY_HOSTS
    ]


def _pick_output_file(work_dir: Path, info: dict) -> Path:
    final_path = info.get("filepath") or info.get("_filename")
    if final_path and Path(final_path).is_file():
        return Path(final_path)
    requested = info.get("requested_downloads") or []
    for item in requested:
        filepath = item.get("filepath")
        if filepath:
            candidate = Path(filepath)
            if candidate.exists() and candidate.is_file():
                return candidate

    candidates = [
        p
        for p in work_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
        and not p.name.endswith(".part")
    ]
    if not candidates:
        raise NoMediaFileError("Сайт не выдал готовый видеофайл. Попробуйте другую ссылку; если ошибка повторяется, нужны логи загрузки.")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _check_size(path: Path, max_upload_mb: int) -> int:
    size_bytes = path.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    if size_mb > max_upload_mb:
        raise FileTooLargeError(size_mb, max_upload_mb)
    return size_bytes


def _download_with_ytdlp_once(
    url: str,
    platform: str,
    work_dir: Path,
    max_upload_mb: int,
    cookies_file: Path | None,
    ffmpeg_location: str | None,
    source_label: str = "yt-dlp",
    audio: bool = False,
    max_height: int = 720,
    russian: bool = False,
) -> DownloadResult:
    download_log = _DownloadLogger(max_upload_mb)
    ydl_opts: dict = {
        "logger": download_log,
        # Prefer a ready-to-send single MP4 file so hosting without FFmpeg still works.
        "format": "b[ext=mp4]/b",
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
    }

    if platform == "YouTube":
        # YouTube usually supplies separate audio/video streams.
        ydl_opts["format"] = (f"bv[vcodec^=avc1][height<={max_height}]+ba[acodec^=mp4a]/"
                              f"b[vcodec^=avc1][acodec^=mp4a][height<={max_height}]/"
                              f"bv[height<={max_height}]+ba/b[height<={max_height}]")
        ydl_opts["merge_output_format"] = "mp4"
        ydl_opts["js_runtimes"] = youtube_js_runtimes()
    if russian:
        ydl_opts["format"] = (
            f"bv[vcodec^=avc1][height<={max_height}]+ba[language^=ru]/"
            f"b[vcodec^=avc1][language^=ru][height<={max_height}]/"
            f"bv[height<={max_height}]+ba[language^=ru]/"
            f"b[language^=ru][height<={max_height}]"
        )
    if audio:
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192",
        }]
    ydl_opts["max_filesize"] = max_upload_mb * 1024 * 1024
    ydl_opts["playlistend"] = 1

    if cookies_file is not None:
        ydl_opts["cookiefile"] = str(cookies_file)
    effective_ffmpeg = ffmpeg_location or resolve_ffmpeg()
    if effective_ffmpeg:
        ydl_opts["ffmpeg_location"] = effective_ffmpeg

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    if download_log.skipped_size is not None:
        raise FileTooLargeError(download_log.skipped_size / (1024 * 1024), max_upload_mb)

    if not isinstance(info, dict):
        raise DownloadError("Unexpected response from yt-dlp")

    if audio:
        outputs = list(work_dir.glob("*.mp3"))
        if not outputs:
            raise DownloadError("No MP3 was produced; the video may have no audio")
        output = outputs[0]
    else:
        output = _pick_output_file(work_dir, info)
    if platform == "YouTube" and not audio:
        output = _prepare_telegram_video(output, effective_ffmpeg)
    size_bytes = _check_size(output, max_upload_mb)

    title = str(info.get("title") or info.get("description") or "Видео")
    author = info.get("uploader") or info.get("creator") or info.get("channel")

    return DownloadResult(
        path=output,
        title=title[:200],
        author=str(author)[:100] if author else None,
        platform=platform,
        webpage_url=str(info.get("webpage_url") or url),
        size_bytes=size_bytes,
        work_dir=work_dir,
        width=int(info["width"]) if info.get("width") else None,
        height=int(info["height"]) if info.get("height") else None,
        source=source_label,
    )


def _download_with_ytdlp(url, platform, work_dir, max_upload_mb, cookies_file,
                         ffmpeg_location, source_label="yt-dlp", audio=False, russian=False):
    heights = (720, 480, 360, 240) if platform == "YouTube" and not audio else (720,)
    size_error = None
    for height in heights:
        try:
            return _download_with_ytdlp_once(
                url, platform, work_dir, max_upload_mb, cookies_file,
                ffmpeg_location, source_label, audio, max_height=height, russian=russian,
            )
        except FileTooLargeError as exc:
            size_error = exc
            logger.info('File exceeds %s MB at %sp; trying smaller format', max_upload_mb, height)
            _clear_work_dir(work_dir)
        except yt_dlp.utils.DownloadError as exc:
            if size_error is None or 'Requested format is not available' not in str(exc):
                raise
            _clear_work_dir(work_dir)
    raise size_error


def _extract_proxy_media(client: httpx.Client, proxy_url: str) -> tuple[str, str, str | None]:
    response = client.get(proxy_url)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "").lower()
    if content_type.startswith("video/"):
        return str(response.url), "Instagram video", None

    parser = _MetaParser()
    parser.feed(response.text)

    media_url = None
    for key in VIDEO_META_KEYS:
        value = parser.meta.get(key)
        if value:
            media_url = html.unescape(value)
            break

    if not media_url:
        raise DownloadError(f"No direct video metadata found at {proxy_url}")

    title = html.unescape(
        parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or "Instagram video"
    )
    author = parser.meta.get("og:site_name")
    return media_url, title, author


def _download_direct_media(
    client: httpx.Client,
    media_url: str,
    target: Path,
    max_upload_mb: int,
) -> int:
    max_bytes = max_upload_mb * 1024 * 1024

    with client.stream("GET", media_url) as response:
        response.raise_for_status()

        content_length = response.headers.get("content-length")
        if content_length:
            try:
                announced = int(content_length)
            except ValueError:
                announced = 0
            if announced > max_bytes:
                raise FileTooLargeError(announced / (1024 * 1024), max_upload_mb)

        total = 0
        with target.open("wb") as file:
            for chunk in response.iter_bytes(chunk_size=1024 * 256):
                total += len(chunk)
                if total > max_bytes:
                    raise FileTooLargeError(total / (1024 * 1024), max_upload_mb)
                file.write(chunk)

    return total


def _download_instagram_via_proxy(
    original_url: str,
    work_dir: Path,
    max_upload_mb: int,
) -> DownloadResult:
    errors: list[str] = []

    with httpx.Client(
        headers=DEFAULT_HEADERS,
        follow_redirects=True,
        timeout=httpx.Timeout(30.0, read=90.0),
    ) as client:
        for proxy_url in build_instagram_proxy_urls(original_url):
            try:
                media_url, title, author = _extract_proxy_media(client, proxy_url)
                target = work_dir / "instagram_proxy.mp4"
                size_bytes = _download_direct_media(client, media_url, target, max_upload_mb)

                return DownloadResult(
                    path=target,
                    title=title[:200],
                    author=author[:100] if author else None,
                    platform="Instagram",
                    webpage_url=original_url,
                    size_bytes=size_bytes,
                    work_dir=work_dir,
                    width=None,
                    height=None,
                    source=urlparse(proxy_url).hostname or "proxy",
                )
            except FileTooLargeError:
                raise
            except Exception as exc:
                errors.append(f"{proxy_url}: {exc}")

    raise DownloadError("Instagram proxy fallback failed: " + " | ".join(errors))


def _clear_work_dir(work_dir: Path) -> None:
    for item in work_dir.iterdir():
        if item.is_file() or item.is_symlink():
            try:
                item.unlink()
            except OSError:
                pass
        elif item.is_dir():
            shutil.rmtree(item, ignore_errors=True)


def download_video(
    url: str,
    platform: str,
    download_root: Path,
    max_upload_mb: int,
    cookies_file: Path | None = None,
    ffmpeg_location: str | None = None,
) -> DownloadResult:
    work_dir = download_root / uuid.uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=False)

    public_error: Exception | None = None
    auth_error: Exception | None = None

    if platform == "Instagram":
        logger.info(
            "Instagram download started; service session configured: %s",
            "yes" if cookies_file is not None else "no",
        )

    try:
        if platform == "Pinterest":
            photo = _download_pinterest_photo(url, work_dir, max_upload_mb)
            if photo is not None:
                return photo
        # 1) Always try a public download first. This keeps TikTok and
        # public Instagram links independent from the service account.
        try:
            return _download_with_ytdlp(
                url=url,
                platform=platform,
                work_dir=work_dir,
                max_upload_mb=max_upload_mb,
                cookies_file=None,
                ffmpeg_location=ffmpeg_location,
                source_label="yt-dlp-public",
            )
        except FileTooLargeError:
            raise
        except Exception as exc:
            public_error = exc
            if platform == "Instagram":
                logger.warning("Instagram public download failed: %s", exc)

        # 2) Instagram only: retry with the bot's server-side service session.
        # Users never need to provide their own Instagram credentials.
        if platform == "Instagram" and cookies_file is not None:
            _clear_work_dir(work_dir)
            try:
                return _download_with_ytdlp(
                    url=url,
                    platform=platform,
                    work_dir=work_dir,
                    max_upload_mb=max_upload_mb,
                    cookies_file=cookies_file,
                    ffmpeg_location=ffmpeg_location,
                    source_label="yt-dlp-service-session",
                )
            except FileTooLargeError:
                raise
            except Exception as exc:
                auth_error = exc
                logger.warning("Instagram service-session download failed: %s", exc)

        # 3) Best-effort public metadata fallback.
        if platform == "Instagram":
            _clear_work_dir(work_dir)
            try:
                return _download_instagram_via_proxy(
                    original_url=url,
                    work_dir=work_dir,
                    max_upload_mb=max_upload_mb,
                )
            except FileTooLargeError:
                raise
            except Exception as proxy_exc:
                logger.warning("Instagram proxy fallback failed: %s", proxy_exc)
                auth_state = (
                    f"service session failed: {auth_error}; "
                    if cookies_file is not None
                    else "service session is not configured; "
                )
                raise DownloadError(
                    f"Instagram public download failed: {public_error}; "
                    f"{auth_state}"
                    f"proxy fallback failed: {proxy_exc}"
                ) from proxy_exc

        if isinstance(public_error, DownloadError):
            raise public_error
        if isinstance(public_error, yt_dlp.utils.DownloadError):
            raise DownloadError(str(public_error)) from public_error
        raise DownloadError(str(public_error) if public_error else "Unknown download error")

    except FileTooLargeError:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except DownloadError:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except yt_dlp.utils.DownloadError as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise DownloadError(str(exc)) from exc
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise


def _ffmpeg_binary(location: str | None) -> str:
    location = location or resolve_ffmpeg()
    if location:
        path = Path(location)
        if path.is_dir():
            path = path / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        binary = str(path)
    else:
        binary = shutil.which("ffmpeg")
    if not binary or not Path(binary).is_file():
        raise DownloadError("Для MP3 и YouTube нужен FFmpeg на сервере (FFMPEG_LOCATION).")
    return binary


def convert_to_mp3(result: DownloadResult, max_upload_mb: int,
                   ffmpeg_location: str | None = None) -> DownloadResult:
    target = result.work_dir / "audio.mp3"
    try:
        subprocess.run(
            [_ffmpeg_binary(ffmpeg_location), "-nostdin", "-y", "-v", "error",
             "-i", str(result.path), "-map", "0:a:0", "-vn", "-c:a", "libmp3lame",
             "-b:a", "192k", str(target)],
            check=True, capture_output=True, timeout=300,
        )
        return replace(result, path=target, size_bytes=_check_size(target, max_upload_mb),
                       width=None, height=None)
    except FileTooLargeError:
        result.cleanup()
        raise
    except Exception as exc:
        result.cleanup()
        raise DownloadError("Не удалось получить MP3. Проверьте, есть ли звук в ролике и FFmpeg на сервере.") from exc


def download_audio(url: str, platform: str, download_root: Path,
                   max_upload_mb: int, cookies_file: Path | None = None,
                   ffmpeg_location: str | None = None) -> DownloadResult:
    _ffmpeg_binary(ffmpeg_location)
    if platform == "Instagram":
        result = download_video(url, platform, download_root, max_upload_mb,
                                cookies_file, ffmpeg_location)
        return convert_to_mp3(result, max_upload_mb, ffmpeg_location)
    work_dir = download_root / uuid.uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=False)
    try:
        return _download_with_ytdlp(url, platform, work_dir, max_upload_mb,
                                   None, ffmpeg_location, audio=True)
    except Exception as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        if isinstance(exc, DownloadError):
            raise
        raise DownloadError("Не удалось получить MP3 из ролика.") from exc


def _pinterest_pin_id(client: httpx.Client, url: str) -> str:
    for _ in range(6):
        parsed = urlparse(url)
        if SUPPORTED_HOSTS.get((parsed.hostname or "").lower()) != "Pinterest":
            raise DownloadError("Ссылка должна вести на пин Pinterest.")
        match = re.fullmatch(r"/pin/(?:[\w-]+--)?(\d+)/?", parsed.path)
        if match:
            return match.group(1)
        response = client.get(url, follow_redirects=False)
        if not response.is_redirect:
            response.raise_for_status()
        location = response.headers.get("location")
        if not location:
            raise DownloadError("Не удалось раскрыть короткую ссылку Pinterest.")
        url = urljoin(url, location)
    raise DownloadError("Слишком много перенаправлений Pinterest.")


def _pin_photo_url(data: dict) -> str | None:
    # Never send a video's cover as if it were a photo pin.
    if data.get("videos") or data.get("embed") or data.get("story_pin_data"):
        return None
    images = data.get("images") or {}
    candidates = [v for v in images.values() if isinstance(v, dict) and v.get("url")]
    if not candidates:
        raise DownloadError("В этом пине не найдено фото или видео.")
    original = images.get("orig")
    best = original if isinstance(original, dict) and original.get("url") else max(
        candidates, key=lambda v: (v.get("width") or 0) * (v.get("height") or 0))
    url = best["url"]
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "pinimg.com" or host.endswith(".pinimg.com")):
        raise DownloadError("Неизвестный адрес изображения Pinterest.")
    return url


def _download_pinterest_photo(url: str, work_dir: Path, max_upload_mb: int) -> DownloadResult | None:
    with httpx.Client(headers=DEFAULT_HEADERS, timeout=30, follow_redirects=False) as client:
        pin_id = _pinterest_pin_id(client, url)
        response = client.get(
            "https://www.pinterest.com/resource/PinResource/get/",
            params={"data": json.dumps({"options": {
                "field_set_key": "unauth_react_main_pin", "id": pin_id,
            }})},
            headers={"X-Pinterest-PWS-Handler": "www/[username].js"},
        )
        response.raise_for_status()
        data = response.json()["resource_response"]["data"]
        if not isinstance(data, dict):
            raise DownloadError("Пин недоступен.")
        photo_url = _pin_photo_url(data)
        if photo_url is None:
            return None
        suffix = Path(urlparse(photo_url).path).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            suffix = ".jpg"
        target = work_dir / (pin_id + suffix)
        size = _download_direct_media(client, photo_url, target, max_upload_mb)
        author = (data.get("closeup_attribution") or {}).get("full_name")
        return DownloadResult(target, str(data.get("title") or "Pinterest")[:200],
                              author, "Pinterest", url, size, work_dir, source="pinterest-photo")


def _prepare_telegram_video(source: Path, ffmpeg_location: str | None) -> Path:
    """Validate actual streams; produce H.264/yuv420p + AAC with faststart."""
    binary = _ffmpeg_binary(ffmpeg_location)
    try:
        probe = subprocess.run([binary, '-nostdin', '-hide_banner', '-i', str(source)],
                               capture_output=True, text=True, timeout=30)
        streams = probe.stderr
        video = re.search(r'Stream[^\n]*Video: ([^\n]+)', streams)
        audio = re.search(r'Stream[^\n]*Audio: ([^\n]+)', streams)
        if video is None or audio is None:
            raise NoMediaFileError('Скачанный файл не содержит одновременно видео и звук. Файл не отправлен; попробуйте другую ссылку.')
        compatible_video = video.group(1).startswith('h264 ') and 'yuv420p(' in video.group(1)
        compatible_video = compatible_video or (video.group(1).startswith('h264 ') and 'yuv420p,' in video.group(1))
        compatible_audio = audio.group(1).startswith('aac ')
        target = source.with_name(source.stem + '.telegram.mp4')
        args = [binary, '-nostdin', '-y', '-v', 'error', '-i', str(source),
                '-map', '0:v:0', '-map', '0:a:0', '-sn', '-dn']
        if compatible_video:
            args += ['-c:v', 'copy']
        else:
            args += ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '25',
                     '-pix_fmt', 'yuv420p', '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2']
        args += ['-c:a', 'copy'] if compatible_audio else ['-c:a', 'aac', '-b:a', '128k']
        args += ['-movflags', '+faststart', str(target)]
        logger.info('Preparing Telegram MP4: video=%s audio=%s',
                    'copy H264' if compatible_video else 'convert H264',
                    'copy AAC' if compatible_audio else 'convert AAC')
        subprocess.run(args, check=True, capture_output=True, timeout=300)
        return target
    except NoMediaFileError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise NoMediaFileError('Не удалось подготовить совместимое видео для Telegram. Попробуйте более короткий ролик.') from exc
