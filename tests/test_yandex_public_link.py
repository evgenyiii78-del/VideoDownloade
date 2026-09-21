import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from yandex_disk import upload_file


class YandexPublicLinkTests(unittest.TestCase):
    def test_uses_metadata_href_returned_by_publish(self):
        real_client = httpx.Client
        metadata_reads = []

        def handle(request):
            if request.url.host == "uploader.yandex.net":
                request.read()
                return httpx.Response(201)

            if request.url.path.endswith("/upload"):
                return httpx.Response(200, json={"href": "https://uploader.yandex.net/upload"})

            if request.url.path.endswith("/publish"):
                return httpx.Response(
                    200,
                    json={
                        "href": "https://cloud-api.yandex.net/v1/disk/resources?published_meta=1"
                    },
                )

            if request.url.params.get("published_meta") == "1":
                metadata_reads.append(1)
                return httpx.Response(
                    200,
                    json={
                        "public_url": "https://disk.yandex.ru/d/test",
                        "size": 5,
                    },
                )

            if request.method == "PUT":
                return httpx.Response(409)

            if request.url.params.get("fields") == "type":
                return httpx.Response(200, json={"type": "dir"})

            return httpx.Response(200, json={"size": 5})

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "video.mp4"
            path.write_bytes(b"video")
            with patch(
                "yandex_disk.httpx.Client",
                side_effect=lambda **kw: real_client(
                    transport=httpx.MockTransport(handle), **kw
                ),
            ):
                url = upload_file(path, "test-only")

        self.assertEqual(url, "https://disk.yandex.ru/d/test")
        self.assertEqual(len(metadata_reads), 1)

    def test_retries_metadata_until_public_url_appears(self):
        real_client = httpx.Client
        reads = 0

        def handle(request):
            nonlocal reads

            if request.url.host == "uploader.yandex.net":
                request.read()
                return httpx.Response(201)

            if request.url.path.endswith("/upload"):
                return httpx.Response(200, json={"href": "https://uploader.yandex.net/upload"})

            if request.url.path.endswith("/publish"):
                return httpx.Response(200)

            if request.method == "PUT":
                return httpx.Response(409)

            if request.url.params.get("fields") == "type":
                return httpx.Response(200, json={"type": "dir"})

            reads += 1
            if reads < 3:
                return httpx.Response(200, json={"size": 5})
            return httpx.Response(
                200,
                json={"public_url": "https://disk.yandex.ru/d/delayed", "size": 5},
            )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "video.mp4"
            path.write_bytes(b"video")
            with patch(
                "yandex_disk.httpx.Client",
                side_effect=lambda **kw: real_client(
                    transport=httpx.MockTransport(handle), **kw
                ),
            ), patch("yandex_disk.time.sleep", return_value=None):
                url = upload_file(path, "test-only")

        self.assertEqual(url, "https://disk.yandex.ru/d/delayed")
        self.assertEqual(reads, 3)


if __name__ == "__main__":
    unittest.main()
