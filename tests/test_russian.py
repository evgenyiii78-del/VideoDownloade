import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import yt_dlp
from russian import russian_subtitles, download_russian
from downloader import NoMediaFileError, _download_with_ytdlp_once


class RussianTests(unittest.TestCase):
    def test_author_captions_preferred(self):
        track = {'url': 'https://example.com/caption?lang=ru', 'ext': 'vtt'}
        result = russian_subtitles({'subtitles': {'ru': [track]}, 'automatic_captions': {'ru-orig': [track]}})
        self.assertEqual(result, ('ru', [track], False))

    def test_translation_excluded_native_auto_allowed(self):
        translated = {'url': 'https://example.com/caption?lang=en&tlang=ru'}
        self.assertIsNone(russian_subtitles({'automatic_captions': {'ru': [translated]}}))
        native = {'url': 'https://example.com/caption?lang=ru'}
        self.assertEqual(russian_subtitles({'automatic_captions': {'ru-orig': [native]}}), ('ru-orig', [native], True))

    def test_missing_audio_cleans_up_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('russian._download_with_ytdlp', side_effect=yt_dlp.utils.DownloadError('Requested format is not available')):
                with self.assertRaisesRegex(NoMediaFileError, 'не найдена'):
                    download_russian('url', 'YouTube', Path(tmp), 49)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_subtitle_download_never_downloads_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            class FakeYDL:
                def __init__(self, options): self.options = options
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def extract_info(self, url, download):
                    assert not download
                    return {'title': 'Test', 'subtitles': {'ru': [{'url': 'https://example.com/sub', 'ext': 'vtt'}]}}
                def process_ie_result(self, info, download):
                    assert self.options['skip_download']
                    path = Path(self.options['outtmpl']).parent / 'test.ru.vtt'
                    path.write_text('WEBVTT\n')
            with patch('russian.yt_dlp.YoutubeDL', FakeYDL):
                result = download_russian('url', 'YouTube', Path(tmp), 49, subtitles=True)
                self.assertEqual(result.path.suffix, '.vtt')
                result.cleanup()

    def test_russian_format_selector_does_not_choose_english(self):
        with tempfile.TemporaryDirectory() as tmp:
            captured = {}
            class FakeYDL:
                def __init__(self, opts): captured.update(opts)
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def extract_info(self, *args, **kwargs): raise RuntimeError('stop after options')
            with patch('downloader.yt_dlp.YoutubeDL', FakeYDL):
                with self.assertRaises(RuntimeError):
                    _download_with_ytdlp_once('url', 'YouTube', Path(tmp), 49, None, None, russian=True)
            formats = [
                {'format_id':'video', 'url':'https://example.com/v', 'vcodec':'avc1', 'acodec':'none', 'height':360, 'ext':'mp4'},
                {'format_id':'en', 'url':'https://example.com/en', 'vcodec':'none', 'acodec':'mp4a', 'language':'en', 'ext':'m4a'},
                {'format_id':'ru', 'url':'https://example.com/ru', 'vcodec':'none', 'acodec':'mp4a', 'language':'ru', 'ext':'m4a'},
            ]
            with yt_dlp.YoutubeDL({'quiet':True}) as ydl:
                selector = ydl.build_format_selector(captured['format'])
                selected = list(selector({'formats':formats, 'has_merged_format':False, 'incomplete_formats':False}))
                self.assertIn('ru', selected[0]['format_id'])
                self.assertEqual(list(selector({'formats':formats[:2], 'has_merged_format':False, 'incomplete_formats':False})), [])
