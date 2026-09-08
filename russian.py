"""Download existing Russian tracks; never synthesize speech or translate captions."""
from pathlib import Path
import shutil
import uuid
from urllib.parse import parse_qs, urlparse

import yt_dlp
from downloader import (DownloadResult, NoMediaFileError, DownloadError,
                        _download_with_ytdlp, _check_size)
from media_runtime import youtube_js_runtimes


def russian_subtitles(info):
    """Prefer uploaded Russian captions, then native Russian automatic captions."""
    for field, automatic in [('subtitles', False), ('automatic_captions', True)]:
        for language, tracks in (info.get(field) or {}).items():
            if language.lower().split('-')[0] != 'ru':
                continue
            # YouTube's automatic translation is not an existing Russian caption track.
            native = [t for t in tracks if t.get('url') and
                      not parse_qs(urlparse(t['url']).query).get('tlang')]
            if native:
                return language, native, automatic
    return None


def download_russian(url, platform, root, limit, cookies=None, ffmpeg=None, *, subtitles=False):
    if platform != 'YouTube':
        raise NoMediaFileError('Поиск русских дорожек доступен для YouTube.')
    work = root / uuid.uuid4().hex
    work.mkdir(parents=True)
    try:
        if not subtitles:
            try:
                return _download_with_ytdlp(url, platform, work, limit, None, ffmpeg,
                                           source_label='youtube-russian-audio', russian=True)
            except yt_dlp.utils.DownloadError as exc:
                if 'Requested format is not available' in str(exc):
                    raise NoMediaFileError('Доступная русская аудиодорожка не найдена. Можно попробовать русские субтитры.') from exc
                raise
        options = {
            'quiet': True, 'noplaylist': True, 'playlistend': 1,
            'skip_download': True, 'js_runtimes': youtube_js_runtimes(),
            'socket_timeout': 30, 'retries': 2,
            'outtmpl': str(work / '%(id)s.%(ext)s'),
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        if not isinstance(info, dict):
            raise DownloadError('Invalid YouTube metadata')
        selected = russian_subtitles(info)
        if selected is None:
            raise NoMediaFileError('Готовые русские субтитры не найдены. Автоперевод не выполняется.')
        language, tracks, automatic = selected
        # Strip other languages and translated tracks before processing the metadata.
        info['subtitles'] = {language: tracks}
        info['automatic_captions'] = {}
        options.update(writesubtitles=True, subtitleslangs=[language], subtitlesformat='vtt/best')
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.process_ie_result(info, download=True)
        files = [p for p in work.iterdir() if p.suffix in {'.vtt', '.srt', '.ttml', '.srv3', '.json3'}]
        if not files:
            raise NoMediaFileError('Русские субтитры найдены, но сайт не отдал файл. Попробуйте позже.')
        path = files[0]
        return DownloadResult(path, str(info.get('title') or 'Субтитры')[:200], None,
                              platform, url, _check_size(path, limit), work,
                              source='russian-auto-captions' if automatic else 'russian-captions')
    except Exception as exc:
        shutil.rmtree(work, ignore_errors=True)
        if isinstance(exc, DownloadError):
            raise
        raise DownloadError('Не удалось получить русскую дорожку с YouTube.') from exc
