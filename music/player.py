# music/player.py - 최종 버전 (효율적인 믹스 추출)

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
    """YouTube 믹스 큐 매니저 - 효율적인 한 번에 추출"""
    
    def __init__(self, guild_player):
        self.guild_player = guild_player
        self.mix_cache = {}
        self.stream_cache = {}   # 스트림 URL 캐시 추가
        
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
    
    async def get_mix_playlist_with_streams(self, video_id: str) -> List[Dict]:
        """믹스 플레이리스트에서 스트림 URL까지 모두 추출"""
        try:
            # 스트림 캐시 확인
            if video_id in self.stream_cache:
                logger.info(f"📋 스트림 캐시에서 믹스 리스트 사용: {video_id}")
                return self.stream_cache[video_id]
            
            mix_url = self.create_mix_url(video_id)
            logger.info(f"🔍 믹스 플레이리스트 + 스트림 추출: {mix_url}")
            
            # extract_flat=False로 변경하여 모든 스트림 URL 추출
            ydl_opts = {
                'format': 'bestaudio/best',
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,  # 중요: False로 변경하여 스트림 URL 추출
                'playlistend': 35,  # 처리할 곡 수 제한
                'ignoreerrors': True,
                'socket_timeout': 30,
                'retries': 2,
                'cookiefile': 'cookies.txt',
                'extractor_args': {
                    'youtube': {
                        'player_client': ['web'],
                    }
                },
                # 다운로드하지 않고 URL만 추출
                'skip_download': True,
                'writeinfojson': False,
                'writethumbnail': False,
            }
            
            loop = asyncio.get_event_loop()
            
            def extract_playlist_with_streams():
                with YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(mix_url, download=False)
            
            # 더 긴 타임아웃 (모든 스트림 URL 추출이므로)
            playlist_info = await asyncio.wait_for(
                loop.run_in_executor(None, extract_playlist_with_streams),
                timeout=60.0  # 1분 타임아웃
            )
            
            if not playlist_info or 'entries' not in playlist_info:
                logger.warning(f"⚠️ 믹스 플레이리스트 정보 없음: {video_id}")
                return []
            
            songs = []
            for entry in playlist_info['entries']:
                if entry and entry.get('id'):
                    # 스트림 URL이 있는 완전한 정보
                    song_info = {
                        'id': entry['id'],
                        'title': entry.get('title', 'Unknown'),
                        'duration': entry.get('duration', 0),
                        'uploader': entry.get('uploader', 'Unknown'),
                        'url': f"https://www.youtube.com/watch?v={entry['id']}",
                        'stream_url': entry.get('url'),  # 스트림 URL 포함!
                        'formats': entry.get('formats', [])  # 포맷 정보도 포함
                    }
                    
                    # 스트림 URL이 있는 경우만 추가
                    if song_info['stream_url']:
                        songs.append(song_info)
                        logger.debug(f"✅ 스트림 URL 포함: {song_info['title'][:30]}")
                    else:
                        logger.debug(f"⚠️ 스트림 URL 없음: {entry.get('title', 'Unknown')[:30]}")
            
            # 스트림 캐시에 저장 (최대 5개 유지 - 더 많은 데이터이므로)
            if len(self.stream_cache) >= 5:
                oldest_key = next(iter(self.stream_cache))
                del self.stream_cache[oldest_key]
            
            self.stream_cache[video_id] = songs
            
            logger.info(f"✅ 믹스에서 스트림 URL 포함 {len(songs)}곡 추출: {video_id}")
            return songs
            
        except asyncio.TimeoutError:
            logger.error(f"⏰ 믹스 스트림 추출 타임아웃: {video_id}")
            return []
        except Exception as e:
            logger.error(f"❌ 믹스 스트림 추출 실패: {e}")
            return []
    
    async def filter_and_select_songs(self, mix_songs: List[Dict], count: int) -> List[Dict]:
        """스트림 URL이 있는 곡들을 필터링 및 선택"""
        try:
            # 현재 재생 중인 곡 ID
            current_id = ""
            if self.guild_player.current:
                current_url = self.guild_player.current[0].get('video_url', '')
                current_id = self.extract_video_id(current_url) or ""
            
            # 대기열에 있는 곡 ID들
            queue_ids = set()
            for track in self.guild_player.queue:
                if not track.get("loading"):
                    url = track.get('video_url', '')
                    video_id = self.extract_video_id(url)
                    if video_id:
                        queue_ids.add(video_id)
            
            # 필터링: 스트림 URL이 있고, 중복이 아닌 곡들만
            filtered_songs = []
            for song in mix_songs:
                song_id = song.get('id', '')
                duration = song.get('duration', 0)
                stream_url = song.get('stream_url')
                
                if (song_id and 
                    stream_url and  # 스트림 URL 필수
                    song_id != current_id and 
                    song_id not in queue_ids and
                    duration > 30 and
                    duration < 1200):
                    
                    filtered_songs.append(song)
            
            # 요청된 개수만큼 선택
            if len(filtered_songs) > count:
                front_count = min(count * 2 // 3, len(filtered_songs) // 2)
                back_count = count - front_count
                
                front_songs = filtered_songs[:len(filtered_songs)//2]
                back_songs = filtered_songs[len(filtered_songs)//2:]
                
                selected = []
                if front_songs:
                    selected.extend(random.sample(front_songs, min(front_count, len(front_songs))))
                if back_songs and back_count > 0:
                    selected.extend(random.sample(back_songs, min(back_count, len(back_songs))))
                
                if len(selected) < count:
                    remaining = [s for s in filtered_songs if s not in selected]
                    additional_needed = count - len(selected)
                    if remaining:
                        selected.extend(random.sample(remaining, min(additional_needed, len(remaining))))
                
                filtered_songs = selected[:count]
            else:
                filtered_songs = filtered_songs[:count]
            
            logger.info(f"🎯 {len(filtered_songs)}곡 선택됨 (요청: {count}곡, 스트림 URL 포함)")
            return filtered_songs
            
        except Exception as e:
            logger.error(f"❌ 곡 필터링 실패: {e}")
            return []
    
    async def add_mix_songs_by_command(self, video_id: str, count: int = 10) -> Dict:
        """명령어를 통해 믹스에서 곡들 추가 - 한 번에 스트림 URL까지"""
        try:
            if count > 30:
                count = 30
            elif count < 1:
                count = 1
            
            logger.info(f"🎵 효율적인 믹스 큐 시작: {video_id}, {count}곡")
            
            # 한 번에 스트림 URL까지 모두 추출
            mix_songs_with_streams = await self.get_mix_playlist_with_streams(video_id)
            
            if not mix_songs_with_streams:
                return {
                    'success': False,
                    'message': "믹스 플레이리스트를 찾을 수 없습니다.",
                    'added_count': 0
                }
            
            # 필터링 및 선택 (스트림 URL이 있는 곡들 대상)
            selected_songs = await self.filter_and_select_songs(mix_songs_with_streams, count)
            
            if not selected_songs:
                return {
                    'success': False,
                    'message': "추가할 수 있는 새로운 곡이 없습니다.",
                    'added_count': 0
                }
            
            # 즉시 모든 곡을 큐에 추가 (FFmpeg 추출 없이!)
            await self._add_ready_tracks(selected_songs)
            
            return {
                'success': True,
                'message': f"믹스에서 {len(selected_songs)}곡을 대기열에 추가했습니다.",
                'added_count': len(selected_songs)
            }
            
        except Exception as e:
            logger.error(f"❌ 효율적인 믹스 추가 실패: {e}")
            return {
                'success': False,
                'message': "믹스 곡 추가 중 오류가 발생했습니다.",
                'added_count': 0
            }
    
    async def _add_ready_tracks(self, selected_songs: List[Dict]):
        """이미 스트림 URL이 있는 트랙들을 즉시 큐에 추가"""
        try:
            async with self.guild_player._processing_lock:
                for song_info in selected_songs:
                    # 즉시 재생 가능한 트랙 생성
                    ready_track = {
                        "title": f"🎲 {song_info['title'][:85]}",
                        "duration": int(song_info.get("duration", 0)),
                        "user": "YouTube 알고리즘",
                        "id": song_info.get('id', ''),
                        "video_url": song_info['url'],
                        "stream_url": song_info['stream_url'],  # 이미 준비된 스트림 URL!
                        "uploader": song_info.get('uploader', 'Unknown'),
                        "auto_added": True,
                        "from_mix": True
                    }
                    
                    self.guild_player.queue.append(ready_track)
                    logger.info(f"⚡ 즉시 추가: {ready_track['title'][:30]}")
                
                # 한 번에 UI 업데이트
                await self.guild_player.update_ui()
                
            logger.info(f"✅ {len(selected_songs)}곡 즉시 추가 완료")
            
        except Exception as e:
            logger.error(f"❌ 즉시 트랙 추가 오류: {e}")

class GuildPlayer:
    def __init__(self, guild_id, bot):
        # 기존 코드...
        self.guild_id = guild_id
        self.bot = bot
        self.vc = None
        self.queue = []
        self.current = []
        self.channel = None
        self.message = None
        
        # 백그라운드 처리용 스레드 풀
        self.search_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"search-{guild_id}")  # 1개로 제한
        self._processing_lock = asyncio.Lock()
        
        # YouTube 믹스 큐 매니저 추가
        self.youtube_mix_queue = YouTubeMixQueue(self)
        
        # Rate Limit 방지를 위한 UI 업데이트 제한 (더 강화)
        self._last_ui_update = 0
        self._ui_update_cooldown = 3.0  # 3초로 증가
        self._ui_update_task = None
        self._ui_update_blocked = False  # UI 업데이트 차단 플래그

    async def _fully_async_search_and_add(self, query, author):
        """완전 비동기 검색 및 큐 추가 - 재생 루프와 완전 분리"""
        try:
            # UI 업데이트를 최소한으로 제한
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
                # UI 업데이트를 지연시켜서 재생 방해 최소화
                asyncio.create_task(self._delayed_ui_update_safe(2.0))
            
            # 더 긴 지연으로 FFmpeg 충돌 방지
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
                
                # UI 업데이트 지연
                asyncio.create_task(self._delayed_ui_update_safe(1.0))
                logger.info(f"⚡ 새로운 트랙 추가: {real_track['title'][:30]}")
            
            # 음성 연결도 지연 처리
            asyncio.create_task(self._delayed_voice_connection(author.voice.channel))
            
        except Exception as e:
            async with self._processing_lock:
                if 'temp_track' in locals() and temp_track in self.queue:
                    self.queue.remove(temp_track)
                asyncio.create_task(self._delayed_ui_update_safe(1.0))
            
            logger.error(f"❌ 백그라운드 처리 오류: {e}")
            asyncio.create_task(self._send_error_message("❌ 검색 오류가 발생했습니다"))

    async def _delayed_ui_update_safe(self, delay: float):
        """안전한 지연 UI 업데이트 (재생 중일 때는 더 지연)"""
        try:
            # 재생 중이면 더 오래 지연
            if self.vc and self.vc.is_playing():
                delay = max(delay, 5.0)
            
            await asyncio.sleep(delay)
            await self.update_ui()
        except Exception as e:
            logger.error(f"❌ 지연 UI 업데이트 오류: {e}")

    async def _delayed_voice_connection(self, voice_channel):
        """지연된 음성 채널 연결"""
        try:
            await asyncio.sleep(1.0)  # 1초 지연
            await self._ensure_voice_connection(voice_channel)
        except Exception as e:
            logger.error(f"❌ 지연된 음성 연결 오류: {e}")

    async def update_ui(self):
        """UI 업데이트 - 재생 중일 때 더 강한 제한"""
        try:
            current_time = time.time()
            
            # 재생 중일 때는 UI 업데이트를 더 제한
            if self.vc and self.vc.is_playing():
                cooldown = self._ui_update_cooldown * 2  # 6초로 증가
            else:
                cooldown = self._ui_update_cooldown
            
            # 쿨다운 체크
            if current_time - self._last_ui_update < cooldown:
                # 이미 예약된 업데이트가 있으면 취소
                if self._ui_update_task and not self._ui_update_task.done():
                    self._ui_update_task.cancel()
                
                # 지연된 업데이트 예약
                remaining_cooldown = cooldown - (current_time - self._last_ui_update)
                self._ui_update_task = asyncio.create_task(
                    self._delayed_ui_update(remaining_cooldown)
                )
                return
            
            # 즉시 업데이트 실행
            await self._perform_ui_update()
            
        except Exception as e:
            logger.error(f"❌ UI 업데이트 스케줄링 오류: {e}")

    async def _perform_ui_update(self):
        """실제 UI 업데이트 수행 - 재생 방해 최소화"""
        try:
            # 재생 중일 때는 UI 업데이트 건너뛰기
            if self.vc and self.vc.is_playing() and self._ui_update_blocked:
                logger.debug(f"🔄 재생 중이므로 UI 업데이트 건너뛰기")
                return
            
            self._last_ui_update = time.time()
            
            if not self.current:
                embed = discord.Embed(
                    title="🎵 음악 플레이어",
                    description="제목을 입력하여 음악을 재생하세요",
                    color=0x00ff00
                )
                embed.add_field(name="📋 대기중인 곡", value=f"{len(self.queue)}개", inline=True)
                
                if self.queue:
                    next_track = self.queue[0]
                    if next_track.get("loading"):
                        embed.add_field(name="🔍 처리 중", value=next_track["title"], inline=False)
                    else:
                        embed.add_field(name="⏭️ 다음 곡", value=next_track["title"][:50], inline=False)
            else:
                track = self.current[0]
                embed = discord.Embed(
                    title=f"🎵 {track['title']}", 
                    color=0x1DB954
                )
                embed.add_field(
                    name="⏱️ 길이", 
                    value=str(timedelta(seconds=track["duration"])), 
                    inline=True
                )
                embed.add_field(
                    name="📋 대기열", 
                    value=f"{len(self.queue)}개", 
                    inline=True
                )
                embed.add_field(
                    name="👤 요청자", 
                    value=track["user"], 
                    inline=True
                )
                
                if track.get("id"):
                    embed.set_thumbnail(url=f"https://img.youtube.com/vi/{track['id']}/hqdefault.jpg")
            
            # 간단한 타임스탬프만 (재생 방해 최소화)
            embed.set_footer(text=f"{datetime.now().strftime('%H:%M:%S')}")
            
            # 우선순위 낮게 실행
            await asyncio.sleep(0.1)
            await self.message.edit(embed=embed, view=MusicView(self))
            logger.debug(f"🔄 서버 {self.guild_id} UI 업데이트 완료")
            
        except discord.HTTPException as e:
            if e.status == 429:  # Rate Limited
                logger.warning(f"⚠️ 서버 {self.guild_id} Rate Limited - UI 업데이트 차단")
                self._ui_update_blocked = True
                self._ui_update_cooldown = min(self._ui_update_cooldown * 2, 15.0)
                # 차단 해제
                asyncio.create_task(self._unblock_ui_updates())
            else:
                logger.error(f"❌ 서버 {self.guild_id} UI 업데이트 HTTP 오류: {e}")
        except Exception as e:
            logger.error(f"❌ 서버 {self.guild_id} UI 업데이트 실패: {e}")

    async def _unblock_ui_updates(self):
        """UI 업데이트 차단 해제"""
        await asyncio.sleep(10)  # 10초 후 차단 해제
        self._ui_update_blocked = False
        self._ui_update_cooldown = max(self._ui_update_cooldown / 2, 3.0)  # 쿨다운 복구
        logger.info(f"✅ 서버 {self.guild_id} UI 업데이트 차단 해제")

# YouTubeMixQueue 클래스의 _add_ready_tracks 메서드 최적화

class YouTubeMixQueue:
    # 기존 코드...
    
    async def _add_ready_tracks(self, selected_songs: List[Dict]):
        """이미 스트림 URL이 있는 트랙들을 즉시 큐에 추가 - 재생 방해 최소화"""
        try:
            # 배치로 한 번에 추가 (락 시간 최소화)
            tracks_to_add = []
            for song_info in selected_songs:
                ready_track = {
                    "title": f"🎲 {song_info['title'][:85]}",
                    "duration": int(song_info.get("duration", 0)),
                    "user": "YouTube 알고리즘",
                    "id": song_info.get('id', ''),
                    "video_url": song_info['url'],
                    "stream_url": song_info['stream_url'],
                    "uploader": song_info.get('uploader', 'Unknown'),
                    "auto_added": True,
                    "from_mix": True
                }
                tracks_to_add.append(ready_track)
            
            # 매우 짧은 락으로 모든 트랙 한 번에 추가
            async with self.guild_player._processing_lock:
                self.guild_player.queue.extend(tracks_to_add)
                logger.info(f"⚡ 배치 추가 완료: {len(tracks_to_add)}곡")
            
            # UI 업데이트는 지연 처리
            asyncio.create_task(self.guild_player._delayed_ui_update_safe(3.0))
            
            logger.info(f"✅ {len(selected_songs)}곡 즉시 추가 완료")
            
        except Exception as e:
            logger.error(f"❌ 즉시 트랙 추가 오류: {e}")
            
class Player:
    """멀티 서버 플레이어 매니저"""
    def __init__(self, bot):
        self.bot = bot
        self.session = None
        self.guild_players = {}
        
    async def initialize(self):
        """플레이어 초기화"""
        try:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8),
                connector=aiohttp.TCPConnector(limit=10, limit_per_host=5)
            )
            
            for guild in self.bot.guilds:
                if config.guild_settings.is_music_enabled(guild.id):
                    await self.get_or_create_player(guild.id)
            
            self.auto_play.start()
            logger.info("✅ 멀티 서버 플레이어 초기화 완료")
            
        except Exception as e:
            logger.error(f"❌ 플레이어 초기화 실패: {e}")
            raise
    
    async def get_or_create_player(self, guild_id):
        """서버별 플레이어 가져오기 또는 생성"""
        if guild_id not in self.guild_players:
            player = GuildPlayer(guild_id, self.bot)
            if await player.initialize():
                self.guild_players[guild_id] = player
                logger.info(f"🎵 서버 {guild_id} 플레이어 생성됨")
            else:
                logger.warning(f"⚠️ 서버 {guild_id} 플레이어 초기화 실패")
                return None
        
        return self.guild_players.get(guild_id)
    
    async def handle_message(self, message):
        """메시지 처리 - 해당 서버의 플레이어에게 전달"""
        if not message.guild:
            return
        
        player = await self.get_or_create_player(message.guild.id)
        if player:
            await player.handle_message(message)
    
    @tasks.loop(seconds=1.0)
    async def auto_play(self):
        """모든 서버의 자동 재생 처리 - 성능 최적화"""
        for guild_id, player in list(self.guild_players.items()):
            try:
                if player.vc and not player.vc.is_playing():
                    if player.queue:
                        ready_tracks = [t for t in player.queue if not t.get("loading")]
                        if not ready_tracks:
                            continue
                        
                        track = ready_tracks[0]
                        player.queue.remove(track)
                        
                        if not track.get("stream_url"):
                            # UI 업데이트를 지연 처리
                            asyncio.create_task(player._delayed_ui_update_safe(1.0))
                            continue
                        
                        try:
                            source = discord.FFmpegPCMAudio(
                                track["stream_url"],
                                **FFMPEG_OPTIONS
                            )
                            
                            player.current = [track]
                            player.vc.play(source)
                            
                            # UI 업데이트를 지연 처리 (재생 시작 후)
                            asyncio.create_task(player._delayed_ui_update_safe(1.0))
                            
                        except Exception as e:
                            logger.error(f"❌ 서버 {guild_id} 재생 실패: {e}")
                            asyncio.create_task(player._delayed_ui_update_safe(1.0))
                            
                    elif player.current:
                        player.current = []
                        asyncio.create_task(player._delayed_ui_update_safe(1.0))
                    
                    # 자동 종료 체크 (변경 없음)
                    if (player.vc and player.vc.channel and 
                        len(player.vc.channel.members) == 1 and 
                        not player.queue and not player.current):
                        
                        await asyncio.sleep(300)
                        if (player.vc and player.vc.channel and 
                            len(player.vc.channel.members) == 1):
                            await player.stop()
                        
            except Exception as e:
                logger.error(f"❌ 서버 {guild_id} 재생 루프 오류: {e}")
    
    async def cleanup(self):
        """모든 리소스 정리"""
        if self.session:
            await self.session.close()
        
        for player in self.guild_players.values():
            await player.cleanup()
        
        self.guild_players.clear()
    
    def get_player(self, guild_id):
        """특정 서버의 플레이어 가져오기"""
        return self.guild_players.get(guild_id)
    
    async def setup_music_channel(self, guild_id, channel_id):
        """음악 채널 설정"""
        try:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                return False, "채널을 찾을 수 없습니다."
            
            embed = discord.Embed(
                title="🎵 음악 플레이어",
                description="제목을 입력하여 음악을 재생하세요",
                color=0x00ff00
            )
            
            message = await channel.send(embed=embed)
            
            config.guild_settings.set_music_channel(guild_id, channel_id)
            config.guild_settings.set_music_message(guild_id, message.id)
            
            await self.get_or_create_player(guild_id)
            
            return True, f"음악 플레이어가 {channel.mention}에 설정되었습니다."
            
        except Exception as e:
            logger.error(f"❌ 음악 채널 설정 실패: {e}")
            return False, "설정 중 오류가 발생했습니다."
    
    async def shutdown_handler(self):
        """정상 종료 처리"""
        logger.info("🔄 멀티 서버 플레이어 종료 준비 중...")
        await self.cleanup()
        logger.info("🧹 모든 리소스 정리 완료")