import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import _build_service_cookie_file


class ServiceCookieTests(unittest.TestCase):
    def test_ok_cookie_header_builds_netscape_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "INSTAGRAM_SESSIONID": "",
                    "INSTAGRAM_CSRFTOKEN": "",
                    "INSTAGRAM_DS_USER_ID": "",
                    "OK_COOKIES": "foo=bar; session=value=with=equals",
                },
                clear=False,
            ):
                path = _build_service_cookie_file(Path(tmp))

            self.assertIsNotNone(path)
            content = path.read_text(encoding="utf-8")
            self.assertIn(".ok.ru\tTRUE\t/\tTRUE\t2147483647\tfoo\tbar", content)
            self.assertIn(
                ".ok.ru\tTRUE\t/\tTRUE\t2147483647\tsession\tvalue=with=equals",
                content,
            )

    def test_no_service_cookies_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "INSTAGRAM_SESSIONID": "",
                    "INSTAGRAM_CSRFTOKEN": "",
                    "INSTAGRAM_DS_USER_ID": "",
                    "OK_COOKIES": "",
                },
                clear=False,
            ):
                self.assertIsNone(_build_service_cookie_file(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
