import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from downloader import _download_with_ytdlp, FileTooLargeError


class SizeRetryTests(unittest.TestCase):
    def test_silent_size_skip_retries_lower_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            options = []
            class FakeYDL:
                def __init__(self, opts):
                    self.opts = opts
                    options.append(opts)
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def extract_info(self, url, download):
                    if len(options) == 1:
                        self.opts['logger'].debug('[download] File is larger than max-filesize (70000000 bytes > 51380224 bytes). Aborting.')
                        return {'id': 'video'}
                    (work / 'video.mp4').write_bytes(b'video')
                    return {'id': 'video', 'title': 'Video', 'width': 854, 'height': 480}
            with patch('downloader.yt_dlp.YoutubeDL', FakeYDL), patch('downloader._prepare_telegram_video', side_effect=lambda path, _: path):
                result = _download_with_ytdlp('https://youtu.be/test', 'YouTube', work, 49, None, None)
            self.assertEqual(len(options), 2)
            self.assertIn('height<=480', options[1]['format'])
            self.assertEqual(result.height, 480)

    def test_exhausted_sizes_report_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('downloader._download_with_ytdlp_once', side_effect=FileTooLargeError(60, 49)) as download:
                with self.assertRaises(FileTooLargeError):
                    _download_with_ytdlp('url', 'YouTube', Path(tmp), 49, None, None)
                self.assertEqual(download.call_count, 4)

    def test_mp3_does_not_retry_video_resolutions(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('downloader._download_with_ytdlp_once', side_effect=FileTooLargeError(60, 49)) as download:
                with self.assertRaises(FileTooLargeError):
                    _download_with_ytdlp('url', 'YouTube', Path(tmp), 49, None, None, audio=True)
                self.assertEqual(download.call_count, 1)
