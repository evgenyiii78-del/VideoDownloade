"""Upload a completed file and publish a share link, without buffering it in RAM."""
from pathlib import Path
from urllib.parse import urlparse
import uuid
import time
import logging

import httpx

API = "https://cloud-api.yandex.net/v1/disk"
logger = logging.getLogger("video_downloader_bot.yandex_disk")
UPLOAD_TIMEOUT = 600
CHUNK_SIZE = 256 * 1024
PUBLIC_URL_WAIT_SECONDS = 30
API_TIMEOUT = httpx.Timeout(30, connect=15)
# A large upload can legitimately spend more than 30 seconds blocked while the
# remote side/network drains a socket buffer. Keep API calls short, but allow
# the signed upload stream to wait much longer for writes to make progress.
UPLOAD_STREAM_TIMEOUT = httpx.Timeout(connect=15, read=60, write=300, pool=30)


def _file_chunks(stream, check, progress):
    sent = 0
    while True:
        check()
        chunk = stream.read(CHUNK_SIZE)
        if not chunk:
            return
        yield chunk
        sent += len(chunk)
        progress(sent)


class DiskError(Exception):
    """Safe message: never contains tokens, upload URLs or raw server responses."""


def _check(response):
    if response.is_success:
        return
    code = response.status_code
    if code in (401, 403):
        raise DiskError("Нет доступа к Яндекс Диску. Администратору нужно проверить YANDEX_DISK_TOKEN и права приложения.")
    if code in (413, 507):
        raise DiskError("На Яндекс Диске недостаточно места или превышен допустимый размер файла.")
    if code == 429:
        raise DiskError("Яндекс Диск временно ограничил запросы. Попробуйте позже.")
    raise DiskError(f"Яндекс Диск вернул ошибку HTTP {code}. Попробуйте позже.")


def _safe_url(url):
    parsed = urlparse(url)
    host = parsed.hostname or ""
    domains = ("yandex.net", "yandex.ru", "yandex.com", "yadi.sk")
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in (None, 443)
            or not any(host == d or host.endswith('.' + d) for d in domains)):
        raise DiskError("Яндекс Диск вернул некорректную ссылку.")
    return url


def upload_file(path: Path, token: str, folder: str = "VideoDownloaderBot", *, progress=None, cancel=None) -> str:
    deadline = time.monotonic() + UPLOAD_TIMEOUT
    total = path.stat().st_size if path.exists() else 0
    phase = "проверка настроек"

    def check():
        if (cancel is not None and cancel.is_set()) or time.monotonic() >= deadline:
            raise DiskError("Превышено общее время загрузки на Яндекс Диск (10 минут). Попробуйте позже.")

    def report(stage, sent=0):
        nonlocal phase
        if stage != phase:
            logger.info("Yandex Disk stage: %s; file_bytes=%s", stage, total)
        phase = stage
        if progress is not None:
            progress(stage, sent, total)

    if not token:
        raise DiskError("Яндекс Диск не подключён: администратору нужно задать YANDEX_DISK_TOKEN.")
    # One dedicated root-level folder; never overwrite or delete existing files.
    if not folder or folder in (".", "..") or any(c in folder for c in "/\\:"):
        raise DiskError("YANDEX_DISK_FOLDER должен содержать одно имя папки без слешей и двоеточий.")
    root = "disk:/" + folder
    remote = root + "/" + uuid.uuid4().hex + path.suffix.lower()
    headers = {
        "Authorization": "OAuth " + token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=API_TIMEOUT, follow_redirects=False) as client:
            def api(method, endpoint, **params):
                check()
                response = client.request(method, API + endpoint, headers=headers, params=params)
                _check(response)
                return response

            report("проверка папки и доступа")
            check()
            response = client.put(API + "/resources", headers=headers, params={"path": root})
            if response.status_code == 409:
                resource = api("GET", "/resources", path=root, fields="type").json()
                if resource.get("type") != "dir":
                    raise DiskError("На Диске уже есть файл с именем папки бота. Измените YANDEX_DISK_FOLDER.")
            else:
                _check(response)

            report("получение адреса загрузки")
            link = api("GET", "/resources/upload", path=remote, overwrite="false").json()
            href = _safe_url(link["href"])

            # No OAuth header on the signed upload URL; httpx streams the file.
            report("отправка файла")
            with path.open("rb") as stream:
                response = client.put(
                    href,
                    content=_file_chunks(stream, check, lambda sent: report("отправка файла", sent)),
                    headers={
                        "Content-Length": str(path.stat().st_size),
                        "Content-Type": "application/octet-stream",
                    },
                    timeout=UPLOAD_STREAM_TIMEOUT,
                )
            _check(response)

            report("публикация ссылки", total)
            publish_response = api("PUT", "/resources/publish", path=remote)
            publish_data = publish_response.json() if publish_response.content else {}

            # Yandex normally returns an href to the resource metadata after publish.
            # Reading that href is the most reliable way to obtain public_url.
            resource = {}
            metadata_href = publish_data.get("href") if isinstance(publish_data, dict) else None
            if metadata_href:
                report("получение публичной ссылки", total)
                metadata_response = client.get(_safe_url(metadata_href), headers=headers)
                _check(metadata_response)
                resource = metadata_response.json()

            # Some accounts/API responses expose public_url only after a short delay.
            # Poll the resource metadata instead of failing immediately after 5 seconds.
            wait_deadline = min(deadline, time.monotonic() + PUBLIC_URL_WAIT_SECONDS)
            while not resource.get("public_url") and time.monotonic() < wait_deadline:
                report("получение публичной ссылки", total)
                resource = api("GET", "/resources", path=remote, fields="public_url,size").json()
                if resource.get("public_url"):
                    break
                time.sleep(1)

            remote_size = resource.get("size")
            if remote_size is not None and remote_size != path.stat().st_size:
                raise DiskError("Не удалось подтвердить полный размер файла на Яндекс Диске.")

            public_url = resource.get("public_url")
            if not public_url:
                raise DiskError(
                    "Файл загружен на Яндекс Диск, но сервис не вернул публичную ссылку. "
                    "Попробуйте отправить ссылку на видео ещё раз."
                )

            check()
            public_url = _safe_url(public_url)
            logger.info("Yandex Disk upload complete; file_bytes=%s", total)
            return public_url

    except httpx.TimeoutException as exc:
        logger.warning("Yandex Disk timeout: stage=%s; type=%s", phase, type(exc).__name__)
        raise DiskError(f"Яндекс Диск не ответил вовремя. Этап: {phase}. Попробуйте позже.") from None
    except DiskError:
        logger.warning("Yandex Disk failed at stage: %s", phase)
        raise
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        logger.warning("Yandex Disk failed: stage=%s; type=%s", phase, type(exc).__name__)
        raise DiskError(f"Не удалось завершить загрузку на Яндекс Диск. Этап: {phase}. Попробуйте позже.") from None
