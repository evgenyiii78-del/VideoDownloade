import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from downloader import (DownloadResult, DownloadError, FileTooLargeError,
                        extract_supported_url, _pin_photo_url, convert_to_mp3,
                        download_audio, download_video, _download_with_ytdlp,
                        _pinterest_pin_id, _is_tiktok_media_host,
                        _pin_video_info)


class MediaTests(unittest.TestCase):
    def test_new_links(self):
        for url, platform in [
            ('https://youtu.be/abc', 'YouTube'),
            ('https://www.youtube.com/shorts/abc', 'YouTube'),
            ('https://m.youtube.com/watch?v=abc&list=xyz', 'YouTube'),
            ('https://pin.it/abc', 'Pinterest'),
            ('https://ru.pinterest.com/pin/123/', 'Pinterest'),
            ('https://uk.pinterest.com/pin/204984220532938060/', 'Pinterest'),
            ('https://fr.pinterest.com/pin/123/', 'Pinterest'),
        ]:
            self.assertEqual(extract_supported_url(url), (url, platform))
        for url in [
            'https://youtube.com.evil.com/watch?v=x',
            'https://pinterest.com@evil.com/pin/1',
            'https://uk.pinterest.com.evil.com/pin/1',
        ]:
            with self.assertRaises(DownloadError):
                extract_supported_url(url)

    def test_original_photo_and_no_video_cover(self):
        data = {'images': {'orig': {'url': 'https://i.pinimg.com/originals/photo.jpg'},
                           'small': {'url': 'https://i.pinimg.com/small.jpg'}}}
        self.assertIn('originals', _pin_photo_url(data))
        self.assertIsNone(_pin_photo_url(dict(data, videos={'video_list': {}})))
        with self.assertRaises(DownloadError):
            _pin_photo_url({'images': {'orig': {'url': 'https://evil.com/image.jpg'}}})

    def test_pinterest_video_prefers_largest_direct_mp4(self):
        data = {
            'videos': {
                'video_list': {
                    'small': {
                        'url': 'https://v.pinimg.com/videos/small.mp4',
                        'width': 360,
                        'height': 640,
                    },
                    'large': {
                        'url': 'https://v.pinimg.com/videos/large.mp4',
                        'width': 720,
                        'height': 1280,
                    },
                    'hls': {
                        'url': 'https://v.pinimg.com/videos/master.m3u8',
                        'width': 1080,
                        'height': 1920,
                    },
                },
            },
            'images': {
                'orig': {'url': 'https://i.pinimg.com/originals/cover.jpg'},
            },
        }
        url, width, height = _pin_video_info(data)
        self.assertEqual(url, 'https://v.pinimg.com/videos/large.mp4')
        self.assertEqual((width, height), (720, 1280))
        self.assertIsNone(_pin_photo_url(data))

    def test_pinterest_video_rejects_foreign_mp4(self):
        data = {
            'videos': {
                'video_list': {
                    'bad': {'url': 'https://evil.com/video.mp4', 'width': 720, 'height': 1280},
                },
            },
        }
        self.assertIsNone(_pin_video_info(data))

    def test_short_link_redirect_validation(self):
        client = Mock()
        response = Mock(is_redirect=True, headers={
            'location': 'https://www.pinterest.com/pin/123/sent/?invite_code=test',
        })
        client.get.return_value = response
        self.assertEqual(_pinterest_pin_id(client, 'https://pin.it/abc'), '123')
        client.get.assert_called_with(
            'https://api.pinterest.com/url_shortener/abc/redirect/',
            follow_redirects=False,
        )

        # Real pin.it links can use api.pinterest.com as an intermediate hop.
        first = Mock(is_redirect=True, headers={
            'location': 'https://api.pinterest.com/url_shortener/abc/redirect/',
        })
        second = Mock(is_redirect=True, headers={
            'location': 'https://www.pinterest.com/pin/456/',
        })
        client.get.side_effect = [first, second]
        self.assertEqual(_pinterest_pin_id(client, 'https://pin.it/abc'), '456')

        client.get.side_effect = None

        html_response = Mock(is_redirect=False, headers={}, text=(
            '<html><head><meta property="og:url" '
            'content="https://www.pinterest.com/pin/789/"></head></html>'
        ))
        html_response.raise_for_status = Mock()
        client.get.return_value = html_response
        self.assertEqual(_pinterest_pin_id(client, 'https://pin.it/abc'), '789')

        response.headers = {'location': 'http://127.0.0.1/internal'}
        client.get.return_value = response
        with self.assertRaises(DownloadError):
            _pinterest_pin_id(client, 'https://pin.it/abc')

    def test_youtube_audio_postprocess_and_file_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work/'x.mp3').write_bytes(b'audio')
            with patch('downloader.yt_dlp.YoutubeDL') as cls:
                cls.return_value.__enter__.return_value.extract_info.return_value = {
                    'title': 'Song', 'requested_downloads': [{'filepath': str(work/'x.webm')}],
                }
                result = _download_with_ytdlp('https://youtu.be/x', 'YouTube', work, 49, None, None, audio=True)
                opts = cls.call_args.args[0]
                self.assertEqual(opts['format'], 'bestaudio/best')
                self.assertTrue(opts['noplaylist'])
                self.assertEqual(opts['postprocessors'][0]['preferredcodec'], 'mp3')
                self.assertEqual(result.path.suffix, '.mp3')

    def test_social_audio_keeps_existing_download_path(self):
        for url, platform in [
            ('https://instagram.com/reel/x', 'Instagram'),
            ('https://vt.tiktok.com/abc/', 'TikTok'),
        ]:
            with patch('downloader.download_video') as video, patch('downloader.convert_to_mp3') as convert:
                download_audio(url, platform, Path('/tmp'), 49)
                video.assert_called_once()
                convert.assert_called_once_with(video.return_value, 49, None)

    def test_tiktok_uses_fallback_when_ytdlp_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Mock()
            with patch('downloader._download_with_ytdlp', side_effect=RuntimeError('status code 0')), \
                 patch('downloader._download_tiktok_via_tikwm', return_value=sentinel) as fallback:
                result = download_video(
                    'https://vt.tiktok.com/abc/', 'TikTok', Path(tmp), 49
                )
            self.assertIs(result, sentinel)
            fallback.assert_called_once()

    def test_tiktok_media_host_validation(self):
        for host in [
            'v16-webapp-prime.tiktok.com',
            'p16-sign.tiktokcdn.com',
            'v16m-default.akamaized.net',
            'video.byteoversea.com',
            'cdn.tikwm.com',
        ]:
            self.assertTrue(_is_tiktok_media_host(host))
        for host in ['evil.com', 'tiktok.com.evil.com', '127.0.0.1']:
            self.assertFalse(_is_tiktok_media_host(host))

    def test_real_mp3_conversion_and_size_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)/'work'
            work.mkdir()
            source = work/'test.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'sine=frequency=440:duration=1', '-c:a', 'aac', str(source)], check=True)
            result = DownloadResult(source, 'Tone', None, 'Instagram', '', source.stat().st_size, work)
            mp3 = convert_to_mp3(result, 49)
            codec = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                                            'stream=codec_name', '-of', 'csv=p=0', str(mp3.path)], text=True)
            self.assertEqual(codec.strip(), 'mp3')
            with self.assertRaises(FileTooLargeError):
                convert_to_mp3(result, 0)
            self.assertFalse(work.exists())


os.environ.setdefault('BOT_TOKEN', '123456:TEST_TOKEN_FOR_OFFLINE_TESTS')
import bot


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def test_other_user_cannot_use_button(self):
        key = bot.remember_choice(1, 10, 'https://youtu.be/x', 'YouTube')
        query = Mock(data=f'download:audio:{key}', from_user=Mock(id=2), message=Mock(chat_id=10))
        query.answer = AsyncMock()
        with patch('bot.send_download', new_callable=AsyncMock) as send:
            await bot.handle_choice(Mock(callback_query=query), Mock())
            send.assert_not_called()
            query.answer.assert_awaited_once()

    async def test_audio_and_photo_telegram_dispatch_cleanup(self):
        for mode, source, method, suffix in [('audio', 'yt-dlp', 'reply_audio', '.mp3'),
                                             ('video', 'pinterest-photo', 'reply_photo', '.jpg')]:
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)/'work'; work.mkdir()
                path = work/('file'+suffix); path.write_bytes(b'media')
                result = DownloadResult(path, 'Title', None, 'Pinterest', '', 5, work, source=source)
                status = Mock(edit_text=AsyncMock(), delete=AsyncMock())
                message = Mock(reply_text=AsyncMock(return_value=status),
                               reply_audio=AsyncMock(), reply_photo=AsyncMock())
                with patch('bot._download', new_callable=AsyncMock, return_value=result):
                    await bot.send_download(message, Mock(), 'url', 'Pinterest', mode)
                getattr(message, method).assert_awaited_once()
                self.assertFalse(work.exists())

if __name__ == '__main__':
    unittest.main()
