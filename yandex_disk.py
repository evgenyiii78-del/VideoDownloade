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
    headers = {"Authorization": "OAuth " + token,
               "Accept": "application/json", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=httpx.Timeout(30, connect=15), follow_redirects=False) as client:
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
                response = client.put(href, content=_file_chunks(stream, check, lambda sent: report("отправка файла", sent)),
                                      headers={"Content-Length": str(path.stat().st_size),
                                               "Content-Type": "application/octet-stream"},
                                      timeout=httpx.Timeout(30, connect=15))
            _check(response)
            report("публикация ссылки", total)
            api("PUT", "/resources/publish", path=remote)
            resource = {}
            for attempt in range(5):
                resource = api("GET", "/resources", path=remote, fields="public_url,size").json()
                if resource.get("public_url"):
                    break
                time.sleep(1)
            if resource.get("size") != path.stat().st_size:
                raise DiskError("Не удалось подтвердить полный размер файла на Яндекс Диске.")
            check()
            public_url = _safe_url(resource["public_url"])
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
