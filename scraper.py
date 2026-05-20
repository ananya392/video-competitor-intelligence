import re
import json
import logging
import requests
from datetime import datetime
from collections import Counter
from urllib.parse import quote_plus
from yt_dlp import YoutubeDL

logger = logging.getLogger(__name__)

YTDL_OPTIONS = {
    'quiet': True,
    'skip_download': True,
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'no_warnings': True,
}


def parse_number(text, default=0):
    if not text or not isinstance(text, str):
        return default
    text = text.replace('+', '').replace(' views', '').strip().lower()
    multipliers = {'k': 1e3, 'm': 1e6, 'b': 1e9}
    match = re.search(r'([\d,.]+)\s*([kmb]?)', text)
    if not match:
        return default
    try:
        number = float(match.group(1).replace(',', ''))
    except ValueError:
        return default
    suffix = match.group(2)
    return int(number * multipliers.get(suffix, 1))


def parse_duration(duration):
    if not duration or not isinstance(duration, str):
        return 0
    parts = duration.split(':')
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return 0
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    else:
        hours = 0
        minutes = 0
        seconds = parts[0]
    return hours * 3600 + minutes * 60 + seconds


def format_duration(seconds):
    if seconds is None:
        return 'Unknown'
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {sec}s"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def parse_upload_date(value):
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, '%Y%m%d')
    except ValueError:
        return None


def _find_best_channel_match(channels, company_name):
    normalized = company_name.strip().lower()

    def score_channel(title: str) -> float:
        title = title.strip().lower()
        if title == normalized:
            return 100.0
        if title.startswith(normalized):
            return 90.0
        if normalized in title:
            return 70.0
        title_words = set(title.split())
        query_words = set(normalized.split())
        overlap = len(title_words & query_words)
        return 50.0 + overlap if overlap > 0 else 0.0

    best = None
    best_score = -1.0
    for channel in channels:
        title = channel.get('title', '')
        score = score_channel(title)
        if score > best_score or (score == best_score and len(title) < len(best.get('title', '')) if best else False):
            best = channel
            best_score = score
    return best or (channels[0] if channels else None)


def _extract_subscriber_count(text):
    if not text:
        return 0
    if isinstance(text, dict):
        text = text.get('simpleText', '')
    if isinstance(text, str) and text.startswith('@'):
        return 0
    return parse_number(text)


def _extract_yt_initial_data(html):
    match = re.search(r'ytInitialData\s*=\s*(\{.*?\});', html)
    if not match:
        match = re.search(r'var ytInitialData\s*=\s*(\{.*?\});', html)
    if not match:
        return None
    return json.loads(match.group(1))


def fetch_channel_info(company_name):
    try:
        query = quote_plus(company_name)
        url = f"https://www.youtube.com/results?search_query={query}&sp=EgIQAg%3D%3D"
        logger.info("Fetching channel page for: %s", company_name)
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
        data = _extract_yt_initial_data(response.text)
        if not data:
            logger.warning("Unable to parse YouTube search response for %s", company_name)
            raise ValueError('No initial data')

        def walk(node):
            if isinstance(node, dict):
                if 'channelRenderer' in node:
                    yield node['channelRenderer']
                for value in node.values():
                    yield from walk(value)
            elif isinstance(node, list):
                for item in node:
                    yield from walk(item)

        channels = []
        for item in walk(data):
            title_obj = item.get('title') or {}
            if isinstance(title_obj, dict):
                title = title_obj.get('simpleText') or ''
            else:
                title = str(title_obj)
            subscribers = item.get('subscriberCountText') or {}
            description = ''
            description_obj = item.get('descriptionSnippet') or {}
            if isinstance(description_obj, dict):
                runs = description_obj.get('runs', [])
                description = ''.join([run.get('text', '') for run in runs if isinstance(run, dict)])
            navigation = item.get('navigationEndpoint', {}).get('browseEndpoint', {})
            channel_url = navigation.get('canonicalBaseUrl')
            if channel_url and not channel_url.startswith('http'):
                channel_url = 'https://www.youtube.com' + channel_url
            subscriber_text_str = ''
        if isinstance(subscribers, dict):
            subscriber_text_str = subscribers.get('simpleText', '')
        elif isinstance(subscribers, str):
            subscriber_text_str = subscribers
        channels.append({
                'title': title,
                'channel_id': item.get('channelId'),
                'description': description,
                'subscriber_text': subscriber_text_str,
                'subscribers': subscriber_text_str or 'Unknown',
                'subscriber_count': _extract_subscriber_count(subscribers),
                'view_count': 'Unknown',
                'total_views': 0,
                'video_count': 'Unknown',
                'channel_url': channel_url or f"https://www.youtube.com/channel/{item.get('channelId')}",
            })

        channel = _find_best_channel_match(channels, company_name)
        if not channel:
            raise ValueError('No channel match found')
        return channel
    except Exception as e:
        logger.exception("Error fetching channel info for %s: %s", company_name, e)
        return {
            'title': company_name,
            'channel_id': None,
            'description': 'No channel data found.',
            'subscribers': 'Unknown',
            'subscriber_count': 0,
            'view_count': 'Unknown',
            'total_views': 0,
            'video_count': 'Unknown',
            'channel_url': '',
        }


def fetch_videos(company_name, limit=8):
    try:
        query = f"{company_name} YouTube"
        logger.info("Searching videos for: %s (limit=%d)", company_name, limit)
        with YoutubeDL(YTDL_OPTIONS) as ydl:
            info = ydl.extract_info(f'ytsearch{limit}:{query}', download=False)
        items = [item for item in info.get('entries', []) if isinstance(item, dict)]
        videos = []
        for item in items:
            if not item.get('id'):
                continue
            videos.append({
                'title': item.get('title', 'Unknown Title'),
                'video_id': item.get('id'),
                'duration': format_duration(item.get('duration')) if item.get('duration') is not None else '0:00',
                'duration_seconds': item.get('duration') or 0,
                'view_count': int(item.get('view_count') or 0),
                'published': item.get('upload_date') or 'Unknown',
                'link': item.get('webpage_url') or f"https://www.youtube.com/watch?v={item.get('id')}",
                'channel': item.get('uploader') or '',
                'description': item.get('description') or '',
                'tags': item.get('tags') or [],
                'like_count': item.get('like_count') if item.get('like_count') is not None else None,
                'comment_count': item.get('comment_count') if item.get('comment_count') is not None else None,
                'upload_date': item.get('upload_date'),
                'categories': item.get('categories') or [],
                'channel_id': item.get('channel_id'),
                'channel_url': item.get('channel_url'),
                'channel_follower_count': item.get('channel_follower_count'),
                'playlist_count': item.get('playlist_count'),
            })
        logger.info("Found %d video items for %s", len(videos), company_name)
        return videos
    except Exception as e:
        logger.exception("Error fetching videos for %s: %s", company_name, e)
        return []


def enrich_videos_with_engagement(videos, max_details=6):
    if not videos:
        return videos
    with YoutubeDL(YTDL_OPTIONS) as ydl:
        top_videos = sorted(videos, key=lambda v: v['view_count'] or 0, reverse=True)[:max_details]
        logger.info("Enriching top %d videos with engagement data", len(top_videos))
        for video in top_videos:
            if not video.get('video_id'):
                logger.debug("Skipping video without id: %s", video.get('title'))
                continue
            vid = video.get('video_id')
            try:
                logger.debug("Extracting info for video id: %s", vid)
                info = ydl.extract_info(video['link'], download=False)
            except Exception as e:
                logger.warning("yt-dlp failed for %s: %s", video.get('link'), e)
                continue
            if not isinstance(info, dict):
                logger.debug("yt-dlp returned non-dict info for %s", vid)
                continue
            video['like_count'] = info.get('like_count') or 0
            video['comment_count'] = info.get('comment_count') or 0
            if info.get('upload_date'):
                video['upload_date'] = info.get('upload_date')
            if info.get('duration'):
                video['duration_seconds'] = info.get('duration')
                video['duration'] = format_duration(info.get('duration'))
            if info.get('tags'):
                video['tags'] = info.get('tags')
            if info.get('categories'):
                video['categories'] = info.get('categories')
    return videos
