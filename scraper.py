import os
import re
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)
API_BASE_URL = 'https://www.googleapis.com/youtube/v3'
DEFAULT_VIDEO_SEARCH_ORDER = 'relevance'


def get_api_key():
    api_key = os.getenv('YOUTUBE_API_KEY')
    if not api_key:
        raise EnvironmentError('YOUTUBE_API_KEY environment variable is required for YouTube Data API access.')
    return api_key


def _youtube_api_request(path, params=None):
    params = dict(params or {})
    params['key'] = get_api_key()
    url = f'{API_BASE_URL}/{path}'
    logger.debug('YouTube API request: %s %s', url, params)
    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


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


def parse_iso8601_duration(duration):
    if not duration or not isinstance(duration, str):
        return 0
    match = re.match(r'^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$', duration)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
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


def _format_published_date(published_at):
    if not published_at or not isinstance(published_at, str):
        return None
    try:
        dt = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
        return dt.strftime('%Y%m%d')
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


def fetch_channel_info(company_name):
    try:
        logger.info('Fetching channel info for: %s', company_name)
        search = _youtube_api_request('search', {
            'part': 'snippet',
            'q': company_name,
            'type': 'channel',
            'maxResults': 5,
            'order': 'relevance',
        })
        channel_ids = [item.get('id', {}).get('channelId') for item in search.get('items', []) if item.get('id', {}).get('channelId')]
        if not channel_ids:
            raise ValueError('No channel search results')

        details = _youtube_api_request('channels', {
            'part': 'snippet,statistics',
            'id': ','.join(channel_ids),
            'maxResults': len(channel_ids),
        })
        channels = []
        for item in details.get('items', []):
            snippet = item.get('snippet', {})
            stats = item.get('statistics', {})
            subscriber_count = int(stats.get('subscriberCount', 0))
            view_count = int(stats.get('viewCount', 0))
            video_count = int(stats.get('videoCount', 0)) if stats.get('videoCount') else 0
            channels.append({
                'title': snippet.get('title', ''),
                'channel_id': item.get('id'),
                'description': snippet.get('description', ''),
                'subscriber_text': snippet.get('customUrl', '') or '',
                'subscribers': f"{subscriber_count:,}" if subscriber_count else 'Unknown',
                'subscriber_count': subscriber_count,
                'view_count': f"{view_count:,}" if view_count else 'Unknown',
                'total_views': view_count,
                'video_count': video_count or 'Unknown',
                'channel_url': f"https://www.youtube.com/channel/{item.get('id')}",
            })

        channel = _find_best_channel_match(channels, company_name)
        if not channel:
            raise ValueError('No channel match found')
        return channel
    except Exception as e:
        logger.exception('Error fetching channel info for %s: %s', company_name, e)
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


def _build_videos_from_ids(video_ids):
    if not video_ids:
        return []
    response = _youtube_api_request('videos', {
        'part': 'snippet,statistics,contentDetails',
        'id': ','.join(video_ids),
        'maxResults': len(video_ids),
    })
    items_by_id = {item['id']: item for item in response.get('items', []) if item.get('id')}
    videos = []
    for video_id in video_ids:
        item = items_by_id.get(video_id)
        if not item:
            continue
        snippet = item.get('snippet', {})
        stats = item.get('statistics', {})
        content_details = item.get('contentDetails', {})
        duration_seconds = parse_iso8601_duration(content_details.get('duration'))
        upload_date = _format_published_date(snippet.get('publishedAt'))
        videos.append({
            'title': snippet.get('title', 'Unknown Title'),
            'video_id': video_id,
            'duration': format_duration(duration_seconds),
            'duration_seconds': duration_seconds,
            'view_count': int(stats.get('viewCount', 0)),
            'published': upload_date or snippet.get('publishedAt', 'Unknown'),
            'link': f"https://www.youtube.com/watch?v={video_id}",
            'channel': snippet.get('channelTitle', ''),
            'description': snippet.get('description', ''),
            'tags': snippet.get('tags', []),
            'like_count': int(stats.get('likeCount')) if stats.get('likeCount') is not None else None,
            'comment_count': int(stats.get('commentCount')) if stats.get('commentCount') is not None else None,
            'upload_date': upload_date,
            'categories': [snippet.get('categoryId')] if snippet.get('categoryId') else [],
            'channel_id': snippet.get('channelId'),
            'channel_url': f"https://www.youtube.com/channel/{snippet.get('channelId')}" if snippet.get('channelId') else '',
            'channel_follower_count': None,
            'playlist_count': None,
        })
    return videos


def fetch_videos(company_name, limit=8, channel_id=None):
    try:
        logger.info('Fetching videos for: %s (limit=%d)', company_name, limit)
        video_ids = []
        fallback_snippets = {}
        if channel_id:
            channel_details = _youtube_api_request('channels', {
                'part': 'contentDetails',
                'id': channel_id,
            })
            uploads = None
            items = channel_details.get('items', [])
            if items:
                uploads = items[0].get('contentDetails', {}).get('relatedPlaylists', {}).get('uploads')
            if uploads:
                next_page_token = None
                while len(video_ids) < limit:
                    params = {
                        'part': 'snippet,contentDetails',
                        'playlistId': uploads,
                        'maxResults': min(limit - len(video_ids), 50),
                    }
                    if next_page_token:
                        params['pageToken'] = next_page_token
                    playlist_response = _youtube_api_request('playlistItems', params)
                    for item in playlist_response.get('items', []):
                        video_id = item.get('contentDetails', {}).get('videoId')
                        if video_id:
                            video_ids.append(video_id)
                            fallback_snippets[video_id] = item.get('snippet', {})
                    next_page_token = playlist_response.get('nextPageToken')
                    if not next_page_token:
                        break
        if not video_ids:
            search_response = _youtube_api_request('search', {
                'part': 'snippet',
                'q': company_name,
                'type': 'video',
                'maxResults': limit,
                'order': DEFAULT_VIDEO_SEARCH_ORDER,
            })
            for item in search_response.get('items', []):
                video_id = item.get('id', {}).get('videoId')
                if video_id:
                    video_ids.append(video_id)
                    fallback_snippets[video_id] = item.get('snippet', {})

        if not video_ids:
            return []
        return _build_videos_from_ids(video_ids)
    except Exception as e:
        logger.exception('Error fetching videos for %s: %s', company_name, e)
        return []


def enrich_videos_with_engagement(videos, max_details=6):
    if not videos:
        return videos
    top_videos = sorted(videos, key=lambda v: v['view_count'] or 0, reverse=True)[:max_details]
    video_ids = [video['video_id'] for video in top_videos if video.get('video_id')]
    if not video_ids:
        return videos
    try:
        response = _youtube_api_request('videos', {
            'part': 'snippet,statistics,contentDetails',
            'id': ','.join(video_ids),
            'maxResults': len(video_ids),
        })
        details_by_id = {item['id']: item for item in response.get('items', []) if item.get('id')}
        for video in videos:
            details = details_by_id.get(video.get('video_id'))
            if not details:
                continue
            stats = details.get('statistics', {})
            content_details = details.get('contentDetails', {})
            if stats.get('likeCount') is not None:
                video['like_count'] = int(stats.get('likeCount'))
            if stats.get('commentCount') is not None:
                video['comment_count'] = int(stats.get('commentCount'))
            if content_details.get('duration'):
                duration_seconds = parse_iso8601_duration(content_details.get('duration'))
                video['duration_seconds'] = duration_seconds
                video['duration'] = format_duration(duration_seconds)
            snippet = details.get('snippet', {})
            if snippet.get('publishedAt'):
                upload_date = _format_published_date(snippet.get('publishedAt'))
                video['upload_date'] = upload_date
            if snippet.get('tags'):
                video['tags'] = snippet.get('tags')
    except Exception as e:
        logger.exception('Error enriching videos: %s', e)
    return videos
