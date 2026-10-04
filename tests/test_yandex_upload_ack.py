import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from yandex_disk import upload_file


class YandexUploadAckRecoveryTests(unittest.TestCase):
    def test_read_timeout_after_full_upload_recovers_from_remote_metadata(self):
        real_client = httpx.Client
        uploaded = []
        verified = []

        def handle(request):
            if request.url.host == "uploader.yandex.net":
                uploaded.append(request.read())
                raise httpx.ReadTimeout("delayed upload acknowledgement", request=request)

            if request.url.path.endswith("/upload"):
                return httpx.Response(
                    200,
                    json={"href": "https://uploader.yandex.net/upload"},
                )

            if request.url.path.endswith("/publish"):
                return httpx.Response(200)

            if request.method == "PUT":
                # Folder already exists.
                return httpx.Response(409)

            fields = request.url.params.get("fields")
            if fields == "type":
                return httpx.Response(200, json={"type": "dir"})

            if fields == "size,type":
                verified.append(True)
                return httpx.Response(200, json={"type": "file", "size": 5})

            return httpx.Response(
                200,
                json={"public_url": "https://disk.yandex.ru/d/test", "size": 5},
            )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "video.mp4"
            path.write_bytes(b"video")
            with patch(
                "yandex_disk.httpx.Client",
                side_effect=lambda **kw: real_client(
                    transport=httpx.MockTransport(handle), **kw
                ),
            ):
                public_url = upload_file(path, "test-only")

        self.assertEqual(public_url, "https://disk.yandex.ru/d/test")
        self.assertEqual(uploaded, [b"video"])
        self.assertTrue(verified)


if __name__ == "__main__":
    unittest.main()
