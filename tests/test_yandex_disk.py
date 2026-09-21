import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch, Mock, AsyncMock

import httpx
from yandex_disk import upload_file, DiskError, _safe_url


class DiskTests(unittest.TestCase):
    def test_upload_publish_stream_and_unique_names(self):
        paths = []
        uploaded = []
        real_client = httpx.Client
        def handle(request):
            if request.url.host == 'uploader.yandex.net':
                self.assertNotIn('authorization', request.headers)
                uploaded.append(request.read())
                return httpx.Response(201)
            self.assertEqual(request.headers['authorization'], 'OAuth test-only')
            if request.url.path.endswith('/upload'):
                paths.append(request.url.params['path'])
                self.assertEqual(request.url.params['overwrite'], 'false')
                return httpx.Response(200, json={'href': 'https://uploader.yandex.net/upload'})
            if request.url.path.endswith('/publish'):
                return httpx.Response(200)
            if request.method == 'PUT':
                return httpx.Response(409)
            if request.url.params['fields'] == 'type':
                return httpx.Response(200, json={'type': 'dir'})
            return httpx.Response(200, json={'public_url': 'https://disk.yandex.ru/d/test', 'size': 5})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'video.mkv'; path.write_bytes(b'video')
            with patch('yandex_disk.httpx.Client', side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw)):
                for _ in range(2):
                    self.assertEqual(upload_file(path, 'test-only'), 'https://disk.yandex.ru/d/test')
            self.assertEqual(uploaded, [b'video', b'video'])
            self.assertNotEqual(paths[0], paths[1])
            self.assertTrue(path.exists())

    def test_errors_are_safe(self):
        real_client = httpx.Client
        for code in (401, 403, 429, 507, 500):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'video.mp4'; path.write_bytes(b'x')
                transport = httpx.MockTransport(lambda r: httpx.Response(code, text='secret token test-only'))
                with patch('yandex_disk.httpx.Client', side_effect=lambda **kw: real_client(transport=transport, **kw)):
                    with self.assertRaises(DiskError) as exc:
                        upload_file(path, 'test-only')
                    self.assertNotIn('test-only', str(exc.exception))
                    self.assertTrue(path.exists())

    def test_invalid_folder_token_and_urls(self):
        with self.assertRaises(DiskError):
            upload_file(Path('unused'), '')
        for folder in ('../private', 'disk:/', '', '..'):
            with self.assertRaises(DiskError):
                upload_file(Path('unused'), 'test-only', folder)
        for url in ('http://yandex.net/a', 'https://evil.test/a', 'https://yandex.net.evil.test/a', 'https://token@yandex.net/a'):
            with self.assertRaises(DiskError):
                _safe_url(url)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_file_link_failure_and_small_video(self):
        os.environ.setdefault('BOT_TOKEN', '123456:TEST_TOKEN_FOR_OFFLINE_TESTS')
        import bot
        from downloader import DownloadResult
        for size, error in ((1024 * 1024 + 1, None), (1024 * 1024 + 1, DiskError('нет места')), (20, None)):
            with self.subTest(size=size, error=error), tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)/'job'; work.mkdir()
                path = work/'test.mp4'
                with path.open('wb') as stream:
                    stream.truncate(size)
                result = DownloadResult(path, 'test', None, 'YouTube', 'url', size, work)
                status = Mock(edit_text=AsyncMock(), delete=AsyncMock())
                message = Mock(reply_text=AsyncMock(return_value=status), reply_video=AsyncMock())
                settings = replace(bot.SETTINGS, max_upload_mb=1, yandex_disk_token='test-only')
                def upload(*args):
                    self.assertTrue(path.exists())
                    if error:
                        raise error
                    return 'https://disk.yandex.ru/d/test'
                with patch('bot.SETTINGS', settings), patch('bot._download', AsyncMock(return_value=result)), patch('bot.upload_file', side_effect=upload) as put:
                    await bot.send_download(message, Mock(), 'url', 'YouTube', 'video')
                self.assertFalse(work.exists())
                if size > 1024 * 1024:
                    put.assert_called_once()
                    message.reply_video.assert_not_called()
                    if error:
                        self.assertIn('нет места', status.edit_text.call_args.args[0])
                    else:
                        self.assertIn('https://disk.yandex.ru/d/test', message.reply_text.call_args.args[0])
                else:
                    put.assert_not_called()
                    message.reply_video.assert_called_once()

    async def test_stale_compression_button_does_not_start_encoding(self):
        os.environ.setdefault('BOT_TOKEN', '123456:TEST_TOKEN_FOR_OFFLINE_TESTS')
        import bot
        query = Mock(data='download:fit:0123456789abcdef', answer=AsyncMock(), message=Mock())
        with patch('bot.send_download', AsyncMock()) as send:
            await bot.handle_choice(Mock(callback_query=query), Mock())
            send.assert_not_called()
            self.assertIn('Сжатие отключено', query.answer.call_args.args[0])

    async def test_cancellation_keeps_file_until_upload_finishes(self):
        import asyncio
        import threading
        os.environ.setdefault('BOT_TOKEN', '123456:TEST_TOKEN_FOR_OFFLINE_TESTS')
        import bot
        from downloader import DownloadResult
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)/'job'; work.mkdir()
            path = work/'test.mp4'; path.write_bytes(b'video')
            result = DownloadResult(path, 'test', None, 'YouTube', 'url', 5, work)
            status = Mock(edit_text=AsyncMock(), delete=AsyncMock())
            message = Mock(reply_text=AsyncMock(return_value=status))
            started = asyncio.Event()
            release = threading.Event()
            loop = asyncio.get_running_loop()
            def upload(*args):
                loop.call_soon_threadsafe(started.set)
                release.wait(5)
                return 'https://disk.yandex.ru/d/test'
            settings = replace(bot.SETTINGS, max_upload_mb=0, yandex_disk_token='test-only')
            with patch('bot.SETTINGS', settings), patch('bot._download', AsyncMock(return_value=result)), patch('bot.upload_file', side_effect=upload):
                task = asyncio.create_task(bot.send_download(message, Mock(), 'url', 'YouTube', 'video'))
                try:
                    await asyncio.wait_for(started.wait(), 2)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertTrue(path.exists())
                finally:
                    release.set()
                for _ in range(100):
                    if not work.exists():
                        break
                    await asyncio.sleep(.01)
                self.assertFalse(work.exists())


class NoLargeTranscodingTests(unittest.TestCase):
    def test_large_youtube_keeps_streams_small_uses_compatibility(self):
        from downloader import _download_with_ytdlp_once
        for size in (20, 1024 * 1024 + 1):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'source.mkv'
                with path.open('wb') as stream:
                    stream.truncate(size)
                ydl = Mock()
                ydl.__enter__ = Mock(return_value=ydl)
                ydl.__exit__ = Mock(return_value=False)
                ydl.extract_info.return_value = {'filepath': str(path)}
                with patch('downloader.yt_dlp.YoutubeDL', return_value=ydl), patch('downloader._prepare_telegram_video', return_value=path) as convert, patch('downloader._video_display_dimensions', return_value=(2384,1080)):
                    result = _download_with_ytdlp_once('url','YouTube',Path(tmp),512,None,None,telegram_limit_mb=1)
                self.assertEqual(convert.call_count, int(size <= 1024 * 1024))
                self.assertEqual((result.width, result.height), (2384,1080))
                self.assertEqual(result.size_bytes, size)
