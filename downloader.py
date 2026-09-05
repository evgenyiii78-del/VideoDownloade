from __future__ import annotations

import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

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

    def cleanup(self) -> None:
        shutil.rmtree(self.work_dir, ignore_errors=True)


def extract_supported_url(text: str) -> tuple[str, str]:
    for match in URL_RE.finditer(text or ""):
        raw_url = match.group(0).rstrip(".,;:!?)]}\"'")
        parsed = urlparse(raw_url)
        host = (parsed.hostname or "").lower().rstrip(".")
        platform = SUPPORTED_HOSTS.get(host)
        if platform:
            return raw_url, platform
    raise UnsupportedUrlError("No supported Instagram or TikTok URL found")


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

    ydl_opts: dict = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(work_dir / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "overwrites": True,
    }

    if cookies_file is not None:
        ydl_opts["cookiefile"] = str(cookies_file)
    if ffmpeg_location:
        ydl_opts["ffmpeg_location"] = ffmpeg_location

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        if not isinstance(info, dict):
            raise DownloadError("Unexpected response from yt-dlp")

        output = _pick_output_file(work_dir, info)
        size_bytes = output.stat().st_size
        size_mb = size_bytes / (1024 * 1024)
        if size_mb > max_upload_mb:
            raise FileTooLargeError(size_mb, max_upload_mb)

        title = str(info.get("title") or info.get("description") or "Видео")
        author = info.get("uploader") or info.get("creator") or info.get("channel")
        webpage_url = str(info.get("webpage_url") or url)

        return DownloadResult(
            path=output,
            title=title[:200],
            author=str(author)[:100] if author else None,
            platform=platform,
            webpage_url=webpage_url,
            size_bytes=size_bytes,
            work_dir=work_dir,
        )
    except FileTooLargeError:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except yt_dlp.utils.DownloadError as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise DownloadError(str(exc)) from exc
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
