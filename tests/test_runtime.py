import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yt_dlp
from yt_dlp.postprocessor.ffmpeg import FFmpegMergerPP, FFmpegExtractAudioPP
from media_runtime import resolve_ffmpeg, youtube_js_runtimes


class BundledRuntimeTests(unittest.TestCase):
    def test_merge_and_mp3_without_system_binaries(self):
        with patch.dict(os.environ, {'PATH': '', 'FFMPEG_LOCATION': ''}):
            resolve_ffmpeg.cache_clear()
            youtube_js_runtimes.cache_clear()
            ffmpeg = resolve_ffmpeg()
            self.assertTrue(ffmpeg and Path(ffmpeg).is_file())
            runtimes = youtube_js_runtimes()
            node = runtimes['node']['path']
            self.assertEqual(subprocess.check_output([node, '-p', '1+1'], text=True).strip(), '2')
            with tempfile.TemporaryDirectory() as tmp:
                video, audio, output = [str(Path(tmp)/p) for p in ['video.mp4', 'audio.m4a', 'merged.mp4']]
                subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                                'color=size=64x64:duration=1', '-c:v', 'libx264', video], check=True)
                subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                                'sine=frequency=440:duration=1', '-c:a', 'aac', audio], check=True)
                with yt_dlp.YoutubeDL({'ffmpeg_location': ffmpeg, 'quiet': True}) as ydl:
                    merger = FFmpegMergerPP(ydl)
                    info = {'filepath': output, 'ext': 'mp4', '__files_to_merge': [video, audio],
                            'requested_formats': [
                                {'vcodec': 'h264', 'acodec': 'none'},
                                {'vcodec': 'none', 'acodec': 'aac', 'protocol': 'https', 'filepath': audio}]}
                    merger.run(info)
                    self.assertTrue(Path(output).is_file())
                    _, result = FFmpegExtractAudioPP(ydl, preferredcodec='mp3', preferredquality='192').run(info)
                    self.assertEqual(Path(result['filepath']).suffix, '.mp3')
                    self.assertGreater(Path(result['filepath']).stat().st_size, 1000)
            resolve_ffmpeg.cache_clear()
            youtube_js_runtimes.cache_clear()
