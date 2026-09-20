import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from downloader import DownloadResult, _video_display_dimensions
from media_runtime import resolve_ffmpeg
from youtube_fit import fit_video, download_youtube_fit


class FitTests(unittest.TestCase):
    def test_real_two_pass_keeps_geometry_and_fits_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            path = work/'source.mp4'
            ffmpeg = resolve_ffmpeg()
            subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','testsrc2=size=320x180:duration=2',
                            '-f','lavfi','-i','sine=duration=2','-c:v','libx264','-crf','0',
                            '-c:a','aac',str(path)],check=True)
            source = DownloadResult(path,'test',None,'YouTube','url',path.stat().st_size,work)
            before = path.read_bytes()
            result = fit_video(source,0.12,ffmpeg)
            self.assertLessEqual(result.size_bytes,0.12*1024*1024)
            self.assertEqual(_video_display_dimensions(result.path,ffmpeg),(320,180))
            self.assertEqual(path.read_bytes(),before)

    def test_failed_encode_cleans_workdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp)/'work'; work.mkdir()
            result=DownloadResult(work/'source.mp4','test',None,'YouTube','url',1,work)
            with patch('youtube_fit.download_youtube_original',return_value=result) as source, patch('youtube_fit.fit_video',side_effect=RuntimeError('failed')):
                with self.assertRaises(RuntimeError):
                    download_youtube_fit('url',Path(tmp),49)
                self.assertEqual(source.call_args.args[2],512)
                self.assertFalse(work.exists())
