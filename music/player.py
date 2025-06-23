# music/player.py - 최적화된 버전 (별도 스레드 + UI 개선)

import discord
import config
import asyncio
import aiohttp
import logging
import random
import re
import time
from datetime import timedelta, datetime
from discord.ext import tasks
from yt_dlp import YoutubeDL
from ui.controls import MusicView
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
        "-headers \"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\""
    ),
    "options": "-vn -bufsize 512k"
}

# 빠른 정보 추출용 설정
FAST_YDL_OPTIONS = {
    'format': 'bestaudio/best',
    'quiet': True,
    'no_warnings': True,
    'extractaudio': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'extract_flat': False,
    'skip_download': True,
    'cookiefile': 'cookies.txt',
    'socket_timeout': 20,
    'retries': 2,
    'geo_bypass': True,
    'age_limit': None,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    },
    'extractor_args': {
        'youtube': {
            'player_client': ['web'],
        }
    }
}

class YouTubeMixQueue:
    """YouTube 믹스 큐 매니저 - 별도 스레드 사용"""
    
    def __init__(self, guild_player, mix_extraction_executor):
        self.guild_player = guild_player
        self.mix_cache = {}
        self._processing_tasks = {}
        # 믹스 추출 전용 스레드 풀 (플레이어로부터 받음)
        self.mix_executor = mix_extraction_executor
        
    def extract_video_id(self, url: str) -> Optional[str]:
        """YouTube URL에서 비디오 ID 추출"""
        try:
            patterns = [
                r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
                r'(?:embed\/)([0-9A-Za-z_-]{11})',
                r'(?:youtu\.be\/)([0-9A-Za-z_-]{11})'
            ]
            
            for pattern in patterns:
                match = re.search(pattern, url)
                if match:
                    return match.group(1)
            return None
            
        except Exception as e:
            logger.error(f"❌ 비디오 ID 추출 실패: {e}")
            return None
    
    def create_mix_url(self, video_id: str) -> str:
        """비디오 ID를 이용해 믹스 플레이리스트 URL 생성"""
        return f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
    
    async def get_mix_list_fast(self, video_id: str) -> List[Dict]:
        """1단계: 빠른 믹스 목록 추출 (별도 스레드)"""
        try:
            # 캐시 확인
            if video_id in self.mix_cache:
                logger.info(f"📋 캐시에서 믹스 목록 사용: {video_id}")
                return self.mix_cache[video_id]
            
            mix_url = self.create_mix_url(video_id)
            logger.info(f"🚀 빠른 믹스 목록 추출 (별도 스레드): {mix_url}")
            
            # 별도 스레드에서 실행
            loop = asyncio.get_event_loop()
            playlist_info = await asyncio.wait_for(
                loop.run_in_executor(self.mix_executor, self._extract_mix_flat, mix_url),
                timeout=10.0
            )
            
            if not playlist_info or 'entries' not in playlist_info:
                logger.warning(f"⚠️ 믹스 목록 추출 실패: {video_id}")
                return []
            
            # 기본 정보만 포함된 목록 생성
            songs = []
            for entry in playlist_info['entries']:
                if entry and entry.get('id'):
                    song_info = {
                        'id': entry['id'],
                        'title': entry.get('title', 'Unknown'),
                        'duration': entry.get('duration', 0),
                        'uploader': entry.get('uploader', 'Unknown'),
                        'url': f"https://www.youtube.com/watch?v={entry['id']}"
                    }
                    songs.append(song_info)
            
            # 캐시 저장 (3개까지)
            if len(self.mix_cache) >= 3:
                oldest_key = next(iter(self.mix_cache))
                del self.mix_cache[oldest_key]
            
            self.mix_cache[video_id] = songs
            
            logger.info(f"✅ 믹스 목록 {len(songs)}곡 추출 완료 (빠른 모드)")
            return songs
            
        except asyncio.TimeoutError:
            logger.error(f"⏰ 믹스 목록 추출 타임아웃: {video_id}")
            return []
        except Exception as e:
            logger.error(f"❌ 믹스 목록 추출 실패: {e}")
            return []
    
    def _extract_mix_flat(self, mix_url: str):
        """믹스 플레이리스트 추출 (스레드에서 실행)"""
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,  # 빠른 추출
            'playlistend': 25,  # 25곡
            'ignoreerrors': True,
            'socket_timeout': 8,
            'retries': 1,
            'geo_bypass': True,
            'cookiefile': 'cookies.txt'
        }
        
        with YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(mix_url, download=False)
    
    async def extract_single_stream(self, song_info: Dict) -> Optional[Dict]:
        """2단계: 개별 곡의 스트림 URL 추출 (별도 스레드)"""
        try:
            video_url = song_info['url']
            
            # 별도 스레드에서 실행
            loop = asyncio.get_event_loop()
            info = await asyncio.wait_for(
                loop.run_in_executor(self.mix_executor, self._extract_single_stream_sync, video_url),
                timeout=5.0
            )
            
            if info and info.get('url'):
                # 스트림 URL 추가
                complete_song = song_info.copy()
                complete_song['stream_url'] = info['url']
                complete_song['duration'] = info.get('duration', song_info['duration'])
                complete_song['title'] = info.get('title', song_info['title'])
                
                logger.debug(f"✅ 스트림 추출 완료: {complete_song['title'][:30]}")
                return complete_song
            else:
                logger.debug(f"⚠️ 스트림 URL 없음: {song_info['title'][:30]}")
                return None
                
        except asyncio.TimeoutError:
            logger.debug(f"⏰ 스트림 추출 타임아웃: {song_info['title'][:30]}")
            return None
        except Exception as e:
            logger.debug(f"❌ 스트림 추출 오류: {song_info['title'][:30]} - {e}")
            return None
    
    def _extract_single_stream_sync(self, video_url: str):
        """개별 스트림 추출 (스레드에서 실행, 썸네일 제거)"""
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'socket_timeout': 5,
            'retries': 0,
            'cookiefile': 'cookies.txt',
            'ignoreerrors': True,
            'geo_bypass': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['web', 'android'],
                }
            }
        }
        
        with YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(video_url, download=False)
    
    def filter_songs(self, mix_songs: List[Dict], target_count: int) -> List[Dict]:
        """곡 필터링 (중복 제거, 길이 체크 등)"""
        try:
            current_id = ""
            if self.guild_player.current:
                current_url = self.guild_player.current[0].get('video_url', '')
                current_id = self.extract_video_id(current_url) or ""
            
            queue_ids = set()
            for track in self.guild_player.queue:
                if not track.get("loading"):
                    url = track.get('video_url', '')
                    video_id = self.extract_video_id(url)
                    if video_id:
                        queue_ids.add(video_id)
            
            filtered_songs = []
            for song in mix_songs:
                song_id = song.get('id', '')
                duration = song.get('duration', 0)
                
                if (song_id and 
                    song_id != current_id and 
                    song_id not in queue_ids and
                    duration > 30 and
                    duration < 1200):
                    
                    filtered_songs.append(song)
            
            # 랜덤하게 선택
            if len(filtered_songs) > target_count:
                selected = random.sample(filtered_songs, target_count)
            else:
                selected = filtered_songs
            
            logger.info(f"🎯 필터링 완료: {len(selected)}곡 선택됨 (요청: {target_count}곡)")
            return selected
            
        except Exception as e:
            logger.error(f"❌ 곡 필터링 실패: {e}")
            return []
    
    async def add_mix_songs_by_command(self, video_id: str, count: int = 10) -> Dict:
        """메인 메서드: 스트리밍 방식으로 믹스 곡들 추가"""
        try:
            if count > 20:
                count = 20
            elif count < 1:
                count = 1
            
            logger.info(f"🎵 스트리밍 믹스 시작: {video_id}, {count}곡")
            
            # 이미 처리 중인지 확인
            if video_id in self._processing_tasks:
                return {
                    'success': False,
                    'message': "이미 해당 곡의 믹스를 처리 중입니다.",
                    'added_count': 0
                }
            
            # 1단계: 빠른 목록 추출
            mix_songs = await self.get_mix_list_fast(video_id)
            
            if not mix_songs:
                return {
                    'success': False,
                    'message': "믹스 플레이리스트를 찾을 수 없습니다.",
                    'added_count': 0
                }
            
            # 필터링
            selected_songs = self.filter_songs(mix_songs, count)
            
            if not selected_songs:
                return {
                    'success': False,
                    'message': "추가할 수 있는 새로운 곡이 없습니다.",
                    'added_count': 0
                }
            
            # 2단계: 백그라운드에서 스트리밍 처리 시작
            task = asyncio.create_task(
                self._stream_process_songs(video_id, selected_songs)
            )
            self._processing_tasks[video_id] = task
            
            return {
                'success': True,
                'message': f"믹스에서 {len(selected_songs)}곡을 처리 중입니다. 곡들이 하나씩 추가됩니다.",
                'added_count': len(selected_songs)
            }
            
        except Exception as e:
            logger.error(f"❌ 스트리밍 믹스 시작 실패: {e}")
            return {
                'success': False,
                'message': "믹스 처리 중 오류가 발생했습니다.",
                'added_count': 0
            }
    
    async def _stream_process_songs(self, video_id: str, selected_songs: List[Dict]):
        """백그라운드에서 곡들을 하나씩 처리하여 대기열에 추가"""
        try:
            added_count = 0
            total_count = len(selected_songs)
            
            logger.info(f"🎯 스트리밍 처리 시작: {total_count}곡")
            
            for i, song_info in enumerate(selected_songs):
                try:
                    # 개별 스트림 추출
                    complete_song = await self.extract_single_stream(song_info)
                    
                    if complete_song and complete_song.get('stream_url'):
                        # 즉시 대기열에 추가
                        await self._add_single_track(complete_song)
                        added_count += 1
                        
                        logger.info(f"⚡ 즉시 추가 ({added_count}/{total_count}): {complete_song['title'][:40]}")
                        
                        # UI 업데이트 (2곡마다 또는 완료시)
                        if added_count % 2 == 0 or added_count == total_count:
                            asyncio.create_task(self.guild_player._delayed_ui_update_safe(1.0))
                    else:
                        logger.debug(f"⚠️ 스트림 추출 실패, 건너뛰기: {song_info['title'][:30]}")
                    
                    # 다음 곡 처리 전 짧은 지연 (과부하 방지)
                    await asyncio.sleep(0.5)
                    
                except Exception as e:
                    logger.debug(f"❌ 개별 곡 처리 오류: {song_info['title'][:30]} - {e}")
                    continue
            
            logger.info(f"✅ 스트리밍 처리 완료: {added_count}/{total_count}곡 추가됨")
            
            # 처리 완료 후 재생 시작 시도
            await self.guild_player._try_start_playback()
            
        except Exception as e:
            logger.error(f"❌ 스트리밍 처리 오류: {e}")
        finally:
            # 처리 완료, 태스크 제거
            if video_id in self._processing_tasks:
                del self._processing_tasks[video_id]
    
    async def _add_single_track(self, song_info: Dict):
        """단일 트랙을 대기열에 즉시 추가"""
        try:
            ready_track = {
                "title": song_info['title'][:85],
                "duration": int(song_info.get("duration", 0)),
                "user": "YouTube 알고리즘",
                "id": song_info.get('id', ''),
                "video_url": song_info['url'],
                "stream_url": song_info['stream_url'],
                "uploader": song_info.get('uploader', 'Unknown'),
                "auto_added": True,
                "from_mix": True
            }
            
            async with self.guild_player._processing_lock:
                self.guild_player.queue.append(ready_track)
            
            # 재생 시작 시도 (이미 재생 중이면 무시됨)
            await self.guild_player._try_start_playback()
            
        except Exception as e:
            logger.error(f"❌ 단일 트랙 추가 오류: {e}")
    
    async def cleanup(self):
        """리소스 정리"""
        try:
            # 모든 처리 중인 태스크 취소
            for task in self._processing_tasks.values():
                task.cancel()
            self._processing_tasks.clear()
            
            # 스레드 풀은 플레이어에서 관리하므로 여기서는 종료하지 않음
            logger.info(f"🧹 믹스 큐 리소스 정리 완료")
            
        except Exception as e:
            logger.error(f"❌ 믹스 큐 리소스 정리 오류: {e}")

class GuildPlayer:
    def __init__(self, guild_id, bot):
        self.guild_id = guild_id
        self.bot = bot
        self.vc = None
        self.queue = []
        self.current = []
        self.channel = None
        self.message = None
        
        # 검색 전용 스레드 풀
        self.search_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"search-{guild_id}")
        # 믹스 추출 전용 스레드 풀 (검색과 완전 분리)
        self.mix_extraction_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"mix-extract-{guild_id}")
        self._processing_lock = asyncio.Lock()
        
        # 믹스 큐 (별도 스레드 풀 사용)
        self.youtube_mix_queue = YouTubeMixQueue(self, self.mix_extraction_executor)
        
        # UI 업데이트 제한
        self._last_ui_update = 0
        self._ui_update_cooldown = 3.0
        self._ui_update_task = None
        self._ui_update_blocked = False

    async def initialize(self):
        """플레이어 초기화"""
        try:
            self.channel = self.bot.get_channel(config.guild_settings.get_music_channel(self.guild_id))
            if not self.channel:
                logger.warning(f"❌ 서버 {self.guild_id} 음악 채널을 찾을 수 없음")
                return False
            
            message_id = config.guild_settings.get_music_message(self.guild_id)
            if message_id:
                try:
                    self.message = await self.channel.fetch_message(message_id)
                except discord.NotFound:
                    embed = discord.Embed(
                        title="🎵 음악 플레이어",
                        description="제목을 입력하여 음악을 재생하세요",
                        color=0x00ff00
                    )
                    self.message = await self.channel.send(embed=embed, view=MusicView(self))
                    config.guild_settings.set_music_message(self.guild_id, self.message.id)
            
            logger.info(f"✅ 서버 {self.guild_id} 플레이어 초기화 완료")
            return True
            
        except Exception as e:
            logger.error(f"❌ 서버 {self.guild_id} 플레이어 초기화 실패: {e}")
            return False

    async def handle_message(self, message):
        """메시지 처리"""
        if (message.channel.id != config.guild_settings.get_music_channel(self.guild_id) or 
            message.author.bot):
            return
        
        if not message.author.voice or not message.author.voice.channel:
            await message.delete()
            temp_msg = await message.channel.send("❌ 음성 채널에 먼저 참여해주세요.")
            await asyncio.sleep(3)
            await temp_msg.delete()
            return
        
        query = message.content.strip()
        if not query:
            await message.delete()
            return
        
        await message.delete()
        asyncio.create_task(self._fully_async_search_and_add(query, message.author))

    async def _fully_async_search_and_add(self, query, author):
        """완전 비동기 검색 및 큐 추가"""
        try:
            async with self._processing_lock:
                temp_track = {
                    "title": f"🔍 {query[:30]}... 검색 중",
                    "duration": 0,
                    "user": f"<@{author.id}>",
                    "id": "",
                    "video_url": "",
                    "stream_url": None,
                    "loading": True
                }
                self.queue.append(temp_track)
                asyncio.create_task(self._delayed_ui_update_safe(2.0))
            
            await asyncio.sleep(0.5)
            
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self.search_executor,
                self._isolated_search_process,
                query
            )
            
            video_url, track_info = result if result else (None, None)
            
            async with self._processing_lock:
                if not video_url or not track_info:
                    if temp_track in self.queue:
                        self.queue.remove(temp_track)
                    
                    asyncio.create_task(self._send_error_message(f"❌ '{query}' 를 찾을 수 없습니다."))
                    asyncio.create_task(self._delayed_ui_update_safe(1.0))
                    return
                
                real_track = {
                    "title": track_info["title"][:95],
                    "duration": int(track_info.get("duration", 0)),
                    "user": f"<@{author.id}>",
                    "id": track_info.get("id", ""),
                    "video_url": video_url,
                    "stream_url": track_info.get("url"),
                    "uploader": track_info.get("uploader", "Unknown")
                }
                
                if temp_track in self.queue:
                    idx = self.queue.index(temp_track)
                    self.queue[idx] = real_track
                else:
                    self.queue.append(real_track)
                
                asyncio.create_task(self._delayed_ui_update_safe(1.0))
                logger.info(f"⚡ 새로운 트랙 추가: {real_track['title'][:30]}")
            
            # 음성 연결 및 재생 시작 시도
            await self._delayed_voice_connection(author.voice.channel)
            await self._try_start_playback()
            
        except Exception as e:
            async with self._processing_lock:
                if 'temp_track' in locals() and temp_track in self.queue:
                    self.queue.remove(temp_track)
                asyncio.create_task(self._delayed_ui_update_safe(1.0))
            
            logger.error(f"❌ 백그라운드 처리 오류: {e}")
            asyncio.create_task(self._send_error_message("❌ 검색 오류가 발생했습니다"))

    async def _try_start_playback(self):
        """재생 시작 시도"""
        try:
            if self.vc and self.vc.is_playing():
                return
            
            if self.current:
                return
            
            ready_tracks = [t for t in self.queue if not t.get("loading") and t.get("stream_url")]
            if not ready_tracks:
                logger.debug(f"🔍 서버 {self.guild_id}: 재생 가능한 곡 없음")
                return
            
            if not self.vc or not self.vc.is_connected():
                logger.debug(f"🔍 서버 {self.guild_id}: 음성 연결 없음")
                return
            
            track = ready_tracks[0]
            self.queue.remove(track)
            
            await self._play_track(track)
            
        except Exception as e:
            logger.error(f"❌ 재생 시작 시도 오류: {e}")

    async def _play_track(self, track):
        """트랙 재생"""
        try:
            stream_url = track.get('stream_url')
            if not stream_url:
                logger.warning(f"⚠️ 스트림 URL 없음: {track['title']}")
                return
            
            audio_source = discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTIONS)
            
            def after_track(error):
                if error:
                    logger.error(f"❌ 재생 오류: {error}")
                else:
                    logger.info(f"✅ 재생 완료: {track['title'][:30]}")
                
                asyncio.run_coroutine_threadsafe(
                    self._handle_track_end(),
                    self.bot.loop
                )
            
            self.vc.play(audio_source, after=after_track)
            self.current = [track]
            
            await self.update_ui()
            logger.info(f"🎵 재생 시작: {track['title'][:50]}")
            
        except Exception as e:
            logger.error(f"❌ 트랙 재생 실패: {track['title'][:30]} - {e}")
            await self._try_start_playback()

    async def _handle_track_end(self):
        """트랙 종료 처리"""
        try:
            self.current = []
            await asyncio.sleep(0.5)
            await self._try_start_playback()
            await self.update_ui()
            
        except Exception as e:
            logger.error(f"❌ 트랙 종료 처리 오류: {e}")

    def _isolated_search_process(self, query):
        """격리된 검색 프로세스"""
        try:
            import aiohttp
            import asyncio
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                result = loop.run_until_complete(self._sync_search_and_extract(query))
                return result
            finally:
                loop.close()
                
        except Exception as e:
            logger.error(f"❌ 격리된 검색 오류: {e}")
            return None, None

    async def _sync_search_and_extract(self, query):
        """동기화된 검색 및 추출"""
        try:
            session = aiohttp.ClientSession()
            try:
                params = {
                    "part": "snippet",
                    "q": query,
                    "type": "video",
                    "key": config.YOUTUBE_API_KEY,
                    "maxResults": 5,
                    "regionCode": "KR",
                    "order": "relevance"
                }
                
                async with session.get(
                    "https://www.googleapis.com/youtube/v3/search", 
                    params=params
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        items = data.get("items", [])
                        
                        for item in items:
                            video_id = item['id']['videoId']
                            video_url = f"https://www.youtube.com/watch?v={video_id}"
                            
                            track_info = await self._extract_track_info(video_url)
                            if track_info:
                                return video_url, track_info
                        
            finally:
                await session.close()
            
            return None, None
            
        except Exception as e:
            logger.error(f"❌ 동기화된 검색 오류: {e}")
            return None, None

    async def _extract_track_info(self, url):
        """트랙 정보 추출 (썸네일 제거)"""
        try:
            loop = asyncio.get_event_loop()
            
            def extract_info():
                with YoutubeDL(FAST_YDL_OPTIONS) as ydl:
                    return ydl.extract_info(url, download=False)
            
            info = await asyncio.wait_for(
                loop.run_in_executor(None, extract_info),
                timeout=10.0
            )
            
            if info:
                return {
                    'title': info.get('title', 'Unknown'),
                    'duration': info.get('duration', 0),
                    'uploader': info.get('uploader', 'Unknown'),
                    'id': info.get('id', ''),
                    'url': info.get('url')
                }
            
            return None
            
        except Exception as e:
            logger.error(f"❌ 트랙 정보 추출 오류: {e}")
            return None

    async def _ensure_voice_connection(self, voice_channel):
        """음성 채널 연결 확인"""
        try:
            if not self.vc or not self.vc.is_connected():
                self.vc = await voice_channel.connect()
                logger.info(f"🔊 서버 {self.guild_id} 음성 채널 연결: {voice_channel.name}")
            elif self.vc.channel != voice_channel:
                await self.vc.move_to(voice_channel)
                logger.info(f"🔄 서버 {self.guild_id} 음성 채널 이동: {voice_channel.name}")
                
        except Exception as e:
            logger.error(f"❌ 서버 {self.guild_id} 음성 연결 오류: {e}")

    async def _delayed_ui_update_safe(self, delay: float):
        """안전한 지연 UI 업데이트"""
        try:
            if self.vc and self.vc.is_playing():
                delay = max(delay, 5.0)
            
            await asyncio.sleep(delay)
            await self.update_ui()
        except Exception as e:
            logger.error(f"❌ 지연 UI 업데이트 오류: {e}")

    async def _delayed_voice_connection(self, voice_channel):
        """지연된 음성 채널 연결"""
        try:
            await asyncio.sleep(1.0)
            await self._ensure_voice_connection(voice_channel)
        except Exception as e:
            logger.error(f"❌ 지연된 음성 연결 오류: {e}")

    async def _send_error_message(self, error_text):
        """오류 메시지 전송"""
        try:
            if self.channel:
                temp_msg = await self.channel.send(error_text)
                await asyncio.sleep(5)
                await temp_msg.delete()
        except Exception as e:
            logger.error(f"❌ 오류 메시지 전송 실패: {e}")

    async def _delayed_ui_update(self, delay):
        """지연된 UI 업데이트"""
        try:
            await asyncio.sleep(delay)
            await self._perform_ui_update()
        except Exception as e:
            logger.error(f"❌ 지연된 UI 업데이트 오류: {e}")

    def get_queue_info(self):
        """대기열 정보 반환"""
        try:
            total_duration = sum(track.get('duration', 0) for track in self.queue if not track.get('loading'))
            
            return {
                'current': self.current[0] if self.current else None,
                'queue_length': len(self.queue),
                'total_duration': total_duration,
                'is_playing': self.vc and self.vc.is_playing() if self.vc else False
            }
        except Exception as e:
            logger.error(f"❌ 대기열 정보 조회 오류: {e}")
            return {
                'current': None,
                'queue_length': 0,
                'total_duration': 0,
                'is_playing': False
            }

    async def stop(self):
        """플레이어 중지"""
        try:
            self.queue.clear()
            self.current = []
            
            if self.vc:
                if self.vc.is_playing():
                    self.vc.stop()
                await self.vc.disconnect()
                self.vc = None
            
            await self.update_ui()
            logger.info(f"🛑 서버 {self.guild_id} 플레이어 중지")
            
        except Exception as e:
            logger.error(f"❌ 서버 {self.guild_id} 플레이어 중지 오류: {e}")

    async def cleanup(self):
        """리소스 정리"""
        try:
            # 믹스 큐 정리
            await self.youtube_mix_queue.cleanup()
            
            # 검색 스레드 풀 종료
            if self.search_executor:
                self.search_executor.shutdown(wait=False)
            
            # 믹스 추출 스레드 풀 종료
            if self.mix_extraction_executor:
                self.mix_extraction_executor.shutdown(wait=False)
            
            await self.stop()
            logger.info(f"🧹 서버 {self.guild_id} 리소스 정리 완료")
            
        except Exception as e:
            logger.error(f"❌ 서버 {self.guild_id} 리소스 정리 오류: {e}")

    async def update_ui(self):
        """UI 업데이트"""
        try:
            current_time = time.time()
            
            if self.vc and self.vc.is_playing():
                cooldown = self._ui_update_cooldown * 2
            else:
                cooldown = self._ui_update_cooldown
            
            if current_time - self._last_ui_update < cooldown:
                if self._ui_update_task and not self._ui_update_task.done():
                    self._ui_update_task.cancel()
                
                remaining_cooldown = cooldown - (current_time - self._last_ui_update)
                self._ui_update_task = asyncio.create_task(
                    self._delayed_ui_update(remaining_cooldown)
                )
                return
            
            await self._perform_ui_update()
            
        except Exception as e:
            logger.error(f"❌ UI 업데이트 스케줄링 오류: {e}")

    async def _perform_ui_update(self):
        """실제 UI 업데이트 수행 - 대기열 표시 제거"""
        try:
            if self.vc and self.vc.is_playing() and self._ui_update_blocked:
                logger.debug(f"🔄 재생 중이므로 UI 업데이트 건너뛰기")
                return
            
            self._last_ui_update = time.time()
            
            if not self.current:
                # 재생 중인 곡이 없을 때
                embed = discord.Embed(
                    title="🎵 음악 플레이어",
                    description="제목을 입력하여 음악을 재생하세요",
                    color=0x00ff00
                )
            else:
                # 재생 중일 때
                current_track = self.current[0]
                
                # YouTube 링크를 포함하여 Discord가 자동으로 썸네일 표시하도록 함
                video_url = current_track.get('video_url', '')
                
                embed = discord.Embed(
                    title="🎵 현재 재생 중",
                    description=f"**{current_track['title']}**\n\n{video_url}",
                    color=0x1DB954
                )
                
                # 재생 시간
                duration = current_track.get('duration', 0)
                if duration > 0:
                    duration_str = f"{duration//60}:{duration%60:02d}"
                    embed.add_field(name="⏱️ 재생시간", value=duration_str, inline=True)
                
                # 요청자
                embed.add_field(name="👤 요청자", value=current_track.get('user', 'Unknown'), inline=True)
                
                # 드롭다운에서 대기열을 확인할 수 있으므로 여기서는 표시하지 않음
            
            if self.message:
                try:
                    await self.message.edit(embed=embed, view=MusicView(self))
                    logger.debug(f"🔄 UI 업데이트 완료: 서버 {self.guild_id}")
                except discord.NotFound:
                    logger.warning(f"⚠️ 메시지 없음: 서버 {self.guild_id}")
                    self.message = None
                except Exception as e:
                    logger.error(f"❌ 메시지 편집 실패: {e}")
            
        except Exception as e:
            logger.error(f"❌ UI 업데이트 수행 오류: {e}")

# 플레이어 매니저
players = {}

def get_player(guild_id, bot):
    """플레이어 인스턴스 가져오기"""
    if guild_id not in players:
        players[guild_id] = GuildPlayer(guild_id, bot)
    return players[guild_id]

async def cleanup_player(guild_id):
    """플레이어 정리"""
    if guild_id in players:
        await players[guild_id].cleanup()
        del players[guild_id]