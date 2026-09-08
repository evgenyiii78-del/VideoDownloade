import subprocess
import tempfile
import unittest
from pathlib import Path
from downloader import _prepare_telegram_video, NoMediaFileError
from media_runtime import resolve_ffmpeg


class VideoCompatibilityTests(unittest.TestCase):
    def test_vp9_video_becomes_h264_with_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'video.mp4'
            ffmpeg = resolve_ffmpeg()
            subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=64x96:rate=10:duration=1',
                            '-f', 'lavfi', '-i', 'sine=duration=1', '-c:v', 'libvpx-vp9', '-c:a', 'aac', str(source)], check=True)
            target = _prepare_telegram_video(source, ffmpeg)
            check = subprocess.run([ffmpeg, '-hide_banner', '-i', str(target)], capture_output=True, text=True)
            self.assertIn('Video: h264', check.stderr)
            self.assertIn('Audio: aac', check.stderr)
            self.assertIn('64x96', check.stderr)
            data = target.read_bytes()
            self.assertLess(data.index(b'moov'), data.index(b'mdat'))
            # The output video stream must really decode, not merely have a thumbnail.
            subprocess.run([ffmpeg, '-v', 'error', '-i', str(target), '-map', '0:v:0', '-f', 'null', '-'], check=True)

    def test_audio_only_is_never_sent_as_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'audio.mp4'
            ffmpeg = resolve_ffmpeg()
            subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'sine=duration=1', '-c:a', 'aac', str(source)], check=True)
            with self.assertRaises(NoMediaFileError):
                _prepare_telegram_video(source, ffmpeg)
