from __future__ import annotations

import html
import re
import shutil
import uuid
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx
import yt_dlp

SUPPORTED_HOSTS = {
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
    raise UnsupportedUrlError("No supported Instagram or TikTok URL found")


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
        raise DownloadError("yt-dlp finished, but no video file was found")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _check_size(path: Path, max_upload_mb: int) -> int:
    size_bytes = path.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    if size_mb > max_upload_mb:
        raise FileTooLargeError(size_mb, max_upload_mb)
    return size_bytes


def _download_with_ytdlp(
    url: str,
    platform: str,
    work_dir: Path,
    max_upload_mb: int,
    cookies_file: Path | None,
    ffmpeg_location: str | None,
    source_label: str = "yt-dlp",
) -> DownloadResult:
    ydl_opts: dict = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
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
    }

    if cookies_file is not None:
        ydl_opts["cookiefile"] = str(cookies_file)
    if ffmpeg_location:
        ydl_opts["ffmpeg_location"] = ffmpeg_location

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    if not isinstance(info, dict):
        raise DownloadError("Unexpected response from yt-dlp")

    output = _pick_output_file(work_dir, info)
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
        source=source_label,
    )


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

    try:
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
