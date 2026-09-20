import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from downloader import _prepare_telegram_video, _video_display_dimensions, _download_with_ytdlp_once
from media_runtime import resolve_ffmpeg


class YouTubeGeometryTests(unittest.TestCase):
    def test_real_landscape_portrait_and_nonsquare_pixels(self):
        ffmpeg = resolve_ffmpeg()
        for size, sar, expected in [('192x108', '1', (192,108)), ('108x192','1',(108,192)), ('144x108','4/3',(192,108))]:
            with self.subTest(size=size), tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp)/'source.mp4'
                subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i',f'testsrc2=size={size}:duration=0.2',
                                '-f','lavfi','-i','sine=duration=0.2','-vf',f'setsar={sar}',
                                '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(src)],check=True)
                output = _prepare_telegram_video(src,ffmpeg)
                self.assertEqual(_video_display_dimensions(output,ffmpeg),expected)

    def test_original_skips_conversion_and_ignores_stale_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            output = work/'original.mkv'
            output.write_bytes(b'original stream')
            captured = {}
            class FakeYDL:
                def __init__(self, opts): captured.update(opts)
                def __enter__(self): return self
                def __exit__(self,*args): pass
                def extract_info(self,*args,**kwargs):
                    return {'filepath':str(output),'width':108,'height':192}
            with patch('downloader.yt_dlp.YoutubeDL',FakeYDL), patch('downloader._prepare_telegram_video') as convert, patch('downloader._video_display_dimensions',return_value=(192,108)):
                result = _download_with_ytdlp_once('url','YouTube',work,49,None,None,original=True)
            convert.assert_not_called()
            self.assertEqual(result.path.read_bytes(), b'original stream')
            self.assertEqual((result.width,result.height),(192,108))
            self.assertNotIn('height',captured['format'])
            self.assertEqual(captured['format'],'bv+ba/b')


class TelegramGeometryTests(unittest.IsolatedAsyncioTestCase):
    async def test_youtube_video_passes_final_dimensions_and_original_is_document(self):
        import os
        os.environ.setdefault('BOT_TOKEN', '123456:TEST_TOKEN_FOR_OFFLINE_TESTS')
        import bot
        from unittest.mock import Mock, AsyncMock
        from downloader import DownloadResult
        for mode in ('video','original'):
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)/'job'; work.mkdir()
                media = work/'test.mp4'; media.write_bytes(b'video')
                result = DownloadResult(media,'test',None,'YouTube','url',5,work,
                                        width=1920,height=1080,source='youtube-original')
                status = Mock(edit_text=AsyncMock(),delete=AsyncMock())
                message = Mock(reply_text=AsyncMock(return_value=status),reply_video=AsyncMock(),reply_document=AsyncMock())
                with patch('bot._download',new_callable=AsyncMock,return_value=result):
                    await bot.send_download(message,Mock(),'url','YouTube',mode)
                if mode=='video':
                    kwargs=message.reply_video.call_args.kwargs
                    self.assertEqual((kwargs['width'],kwargs['height']),(1920,1080))
                else:
                    message.reply_video.assert_not_called()
                    self.assertEqual(message.reply_document.call_args.kwargs['filename'],'youtube_original.mp4')
