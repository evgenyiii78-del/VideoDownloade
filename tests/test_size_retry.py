import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from downloader import _download_with_ytdlp, FileTooLargeError


class SourceSizeTests(unittest.TestCase):
    def test_oversize_never_retries_lower_resolution(self):
        for audio in (False, True):
            with self.subTest(audio=audio), tempfile.TemporaryDirectory() as tmp:
                with patch('downloader._download_with_ytdlp_once', side_effect=FileTooLargeError(60, 49)) as download:
                    with self.assertRaises(FileTooLargeError):
                        _download_with_ytdlp('url', 'YouTube', Path(tmp), 49, None, None, audio=audio)
                    self.assertEqual(download.call_count, 1)
