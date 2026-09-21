"""Upload a completed file and publish a share link, without buffering it in RAM."""
from pathlib import Path
from urllib.parse import urlparse
import uuid
import time

import httpx

API = "https://cloud-api.yandex.net/v1/disk"


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


def upload_file(path: Path, token: str, folder: str = "VideoDownloaderBot") -> str:
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
        with httpx.Client(timeout=60, follow_redirects=False) as client:
            def api(method, endpoint, **params):
                response = client.request(method, API + endpoint, headers=headers, params=params)
                _check(response)
                return response

            response = client.put(API + "/resources", headers=headers, params={"path": root})
            if response.status_code == 409:
                resource = api("GET", "/resources", path=root, fields="type").json()
                if resource.get("type") != "dir":
                    raise DiskError("На Диске уже есть файл с именем папки бота. Измените YANDEX_DISK_FOLDER.")
            else:
                _check(response)
            link = api("GET", "/resources/upload", path=remote, overwrite="false").json()
            href = _safe_url(link["href"])
            # No OAuth header on the signed upload URL; httpx streams the file.
            with path.open("rb") as stream:
                response = client.put(href, content=stream,
                                      headers={"Content-Length": str(path.stat().st_size),
                                               "Content-Type": "application/octet-stream"},
                                      timeout=httpx.Timeout(300, connect=30))
            _check(response)
            api("PUT", "/resources/publish", path=remote)
            resource = {}
            for attempt in range(5):
                resource = api("GET", "/resources", path=remote, fields="public_url,size").json()
                if resource.get("public_url"):
                    break
                time.sleep(1)
            if resource.get("size") != path.stat().st_size:
                raise DiskError("Не удалось подтвердить полный размер файла на Яндекс Диске.")
            return _safe_url(resource["public_url"])
    except DiskError:
        raise
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, AttributeError):
        raise DiskError("Не удалось загрузить файл на Яндекс Диск или получить ссылку. Проверьте подключение и попробуйте позже.") from None
