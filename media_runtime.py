"""Resolve media binaries for Docker and pip-only hosting (no root required)."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def resolve_ffmpeg() -> str | None:
    configured = os.getenv('FFMPEG_LOCATION', '').strip()
    if configured:
        return configured
    system = shutil.which('ffmpeg')
    if system:
        return system
    try:
        import imageio_ffmpeg
        binary = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run([binary, '-version'], check=True, capture_output=True, timeout=10)
        logger.info('Using bundled FFmpeg: %s', binary)
        return binary
    except (ImportError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        logger.warning('FFmpeg unavailable; reinstall requirements.txt: %s', exc)
        return None


@lru_cache(maxsize=1)
def youtube_js_runtimes() -> dict:
    # The binary wheel works even when the hosting does not expose pip scripts in PATH.
    try:
        import nodejs_wheel
        root = Path(nodejs_wheel.__file__).parent
        binary = root / 'node.exe' if os.name == 'nt' else root / 'bin' / 'node'
        subprocess.run([str(binary), '--version'], check=True, capture_output=True, timeout=10)
        logger.info('Using bundled Node.js: %s', binary)
        return {'node': {'path': str(binary)}}
    except (ImportError, OSError, subprocess.SubprocessError):
        return {'deno': {}, 'node': {}}
