from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name, str(default)).strip()
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got: {value!r}") from exc


def _build_instagram_cookie_file(download_dir: Path) -> Path | None:
    """Build a Netscape cookie file from Instagram cookie values stored in .env."""
    sessionid = os.getenv("INSTAGRAM_SESSIONID", "").strip()
    if not sessionid:
        return None

    csrftoken = os.getenv("INSTAGRAM_CSRFTOKEN", "").strip()
    ds_user_id = os.getenv("INSTAGRAM_DS_USER_ID", "").strip()

    cookie_path = download_dir / ".instagram_cookies.txt"
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated at runtime from environment variables. Do not commit this file.",
        f".instagram.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\t{sessionid}",
    ]
    if csrftoken:
        lines.append(f".instagram.com\tTRUE\t/\tTRUE\t2147483647\tcsrftoken\t{csrftoken}")
    if ds_user_id:
        lines.append(f".instagram.com\tTRUE\t/\tTRUE\t2147483647\tds_user_id\t{ds_user_id}")

    cookie_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        cookie_path.chmod(0o600)
    except OSError:
        pass
    return cookie_path


@dataclass(frozen=True)
class Settings:
    bot_token: str
    download_dir: Path
    max_upload_mb: int
    max_concurrent_downloads: int
    cookies_file: Path | None
    ffmpeg_location: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN is not set. Copy .env.example to .env and add your Telegram bot token.")

        download_dir = Path(os.getenv("DOWNLOAD_DIR", "downloads")).expanduser().resolve()
        download_dir.mkdir(parents=True, exist_ok=True)

        cookies_raw = os.getenv("COOKIES_FILE", "").strip()
        cookies_file = Path(cookies_raw).expanduser().resolve() if cookies_raw else None
        if cookies_file is not None and not cookies_file.exists():
            raise RuntimeError(f"COOKIES_FILE does not exist: {cookies_file}")

        if cookies_file is None:
            cookies_file = _build_instagram_cookie_file(download_dir)

        ffmpeg_location = os.getenv("FFMPEG_LOCATION", "").strip() or None

        return cls(
            bot_token=token,
            download_dir=download_dir,
            max_upload_mb=_int_env("MAX_UPLOAD_MB", 49),
            max_concurrent_downloads=max(1, _int_env("MAX_CONCURRENT_DOWNLOADS", 3)),
            cookies_file=cookies_file,
            ffmpeg_location=ffmpeg_location,
        )
