import discord
import config
import asyncio
import aiohttp
import logging
from datetime import timedelta
from discord.ext import tasks
from yt_dlp import YoutubeDL
from ui.controls import MusicView
from concurrent.futures import ThreadPoolExecutor

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

    # 안정적인 HTTP 요청
    'socket_timeout': 20,
    'retries': 2,
    'geo_bypass': True,
    'age_limit': None,

    # User-Agent 지정
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    },

    # 최신 유튜브 대응
    'extractor_args': {
        'youtube': {
            'player_client': ['web'],
        }
    }
}

class Player:
    def __init__(self, bot):
        self.bot = bot
        self.vc = None
        self.queue = []
        self.current = []
        self.channel = None
        self.message = None
        self.session = None
        
        # 백그라운드 처리용 스레드 풀 (재생과 완전 분리)
        self.search_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="search")
        self._processing_lock = asyncio.Lock()  # UI 업데이트 동기화

    async def initialize(self):
        """플레이어 초기화"""
        try:
            self.channel = self.bot.get_channel(config.CHANNEL_ID)
            self.message = await self.channel.fetch_message(config.MSG_ID)
            
            # 메인 세션 생성 (재생용)
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8),
                connector=aiohttp.TCPConnector(limit=10, limit_per_host=5)
            )
            
            self.auto_play.start()
            logging.basicConfig(level=logging.INFO)
            logger.info("✅ Player 초기화 완료 (캐시 없음)")
        except Exception as e:
            logger.error(f"❌ Player 초기화 실패: {e}")
            raise

    async def cleanup(self):
        """리소스 정리"""
        if self.session:
            await self.session.close()
        if self.vc:
            await self.vc.disconnect()
        
        # 스레드 풀 종료
        self.search_executor.shutdown(wait=False)

    async def handle_message(self, message):
        """메시지 처리 - 완전 비동기 처리로 재생 끊김 방지"""
        if message.author == self.bot.user or message.channel.id != config.CHANNEL_ID:
            return

        # 안전한 메시지 삭제
        try:
            await message.delete()
        except (discord.errors.NotFound, discord.errors.Forbidden):
            pass
        except Exception as e:
            logger.warning(f"메시지 삭제 실패: {e}")
        
        # 음성 채널 확인
        if not message.author.voice:
            try:
                await message.channel.send("❌ 음성 채널에 먼저 접속해주세요!", delete_after=3)
            except:
                pass
            return
        
        query = message.content.strip()
        
        # 🚀 완전히 독립적인 백그라운드 처리 (재생과 100% 분리)
        asyncio.create_task(self._fully_async_search_and_add(query, message.author))

    async def _fully_async_search_and_add(self, query, author):
        """완전 비동기 검색 및 큐 추가 - 재생 루프와 완전 분리"""
        try:
            # 1. 즉시 로딩 표시 추가 (UI 반응성 향상)
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
                await self.update_ui()  # 즉시 UI 업데이트
            
            # 2. 완전히 별도 스레드에서 검색 실행 (메인 루프 방해 없음)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self.search_executor,  # 전용 스레드 풀 사용
                self._isolated_search_process,
                query
            )
            
            video_url, track_info = result if result else (None, None)
            
            # 3. 결과 처리 및 UI 업데이트
            async with self._processing_lock:
                if not video_url or not track_info:
                    # 실패시 로딩 트랙 제거
                    if temp_track in self.queue:
                        self.queue.remove(temp_track)
                    
                    asyncio.create_task(self._send_error_message(f"❌ '{query}' 를 찾을 수 없습니다."))
                    await self.update_ui()
                    return
                
                # 성공시 실제 트랙으로 교체
                real_track = {
                    "title": track_info["title"][:95],
                    "duration": int(track_info.get("duration", 0)),
                    "user": f"<@{author.id}>",
                    "id": track_info.get("id", ""),
                    "video_url": video_url,
                    "stream_url": track_info.get("url"),
                    "uploader": track_info.get("uploader", "Unknown")
                }
                
                # 로딩 트랙을 실제 트랙으로 교체
                if temp_track in self.queue:
                    idx = self.queue.index(temp_track)
                    self.queue[idx] = real_track
                else:
                    self.queue.append(real_track)
                
                await self.update_ui()
                logger.info(f"⚡ 새로운 트랙 추가: {real_track['title'][:30]}")
            
            # 4. 음성 연결 (별도 태스크로 분리)
            asyncio.create_task(self._ensure_voice_connection(author.voice.channel))
            
        except Exception as e:
            # 오류시 로딩 트랙 제거
            async with self._processing_lock:
                if temp_track in self.queue:
                    self.queue.remove(temp_track)
                await self.update_ui()
            
            logger.error(f"❌ 백그라운드 처리 오류: {e}")
            asyncio.create_task(self._send_error_message("❌ 검색 오류가 발생했습니다"))

    def _isolated_search_process(self, query):
        """완전히 격리된 검색 프로세스 (별도 스레드용)"""
        try:
            # 새로운 이벤트 루프 생성 (완전 격리)
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            
            try:
                return new_loop.run_until_complete(self._search_with_isolated_session(query))
            finally:
                new_loop.close()
                
        except Exception as e:
            logger.error(f"❌ 격리된 검색 실패: {e}")
            return None, None

    async def _search_with_isolated_session(self, query):
        """격리된 세션으로 검색 수행 - 캐시 없이 매번 새로 검색"""
        # 검색 전용 세션 생성 (메인 세션과 완전 분리)
        connector = aiohttp.TCPConnector(limit=5, limit_per_host=2)
        timeout = aiohttp.ClientTimeout(total=10)
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as search_session:
            try:
                # URL 확인
                if "youtube.com/watch" in query or "youtu.be/" in query:
                    video_url = query
                else:
                    video_url = await self._isolated_search(query, search_session)
                    if not video_url:
                        return None, None

                # 항상 새로운 정보 추출 (캐시 없음)
                logger.info(f"🔄 새로운 정보 추출: {video_url}")
                track_info = await self._isolated_extract(video_url)
                if not track_info:
                    return None, None

                logger.info(f"✅ 검색 완료: {track_info['title'][:30]}")
                return video_url, track_info
                
            except Exception as e:
                logger.error(f"❌ 격리된 검색 오류: {e}")
                return None, None

    async def _isolated_search(self, query, session):
        """격리된 YouTube 검색 - 단순하고 빠른 검색"""
        try:
            params = {
                "part": "snippet",
                "q": query,
                "type": "video",
                "key": config.YOUTUBE_API_KEY,
                "maxResults": 1,
                "regionCode": "KR"
            }

            logger.info(f"🔍 검색: '{query}'")

            async with session.get("https://www.googleapis.com/youtube/v3/search", params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    items = data.get("items", [])
                    
                    if items:
                        video_url = f"https://www.youtube.com/watch?v={items[0]['id']['videoId']}"
                        logger.info(f"✅ 첫번째 결과: {items[0]['snippet']['title'][:50]}")
                        return video_url
                else:
                    logger.warning(f"⚠️ YouTube API 오류: {response.status}")
                        
            logger.error(f"❌ 검색 실패: {query}")
            
        except Exception as e:
            logger.error(f"❌ 검색 오류: {e}")
        return None
    
    def _simple_clean_query(self, query):
        """사용 안함 - 제거됨"""
        return query
    
    def _quick_select_video(self, items, query, is_collection):
        """사용 안함 - 제거됨"""
        return items[0] if items else None

    async def _isolated_extract(self, url):
        """격리된 정보 추출 - 항상 새로운 스트림 URL 생성"""
        try:
            loop = asyncio.get_event_loop()
            
            def extract_info():
                with YoutubeDL(FAST_YDL_OPTIONS) as ydl:
                    return ydl.extract_info(url, download=False)
            
            info = await asyncio.wait_for(
                loop.run_in_executor(None, extract_info),
                timeout=15.0
            )
            
            if not info or not info.get('url'):
                logger.error(f"❌ 스트림 URL 없음")
                return None
                
            logger.info(f"📺 새로운 스트림 URL 생성: {info.get('title', 'Unknown')[:30]}")
            return {
                'title': info.get('title', 'Unknown Title'),
                'duration': info.get('duration', 0),
                'id': info.get('id', ''),
                'uploader': info.get('uploader', 'Unknown'),
                'url': info.get('url')
            }
            
        except asyncio.TimeoutError:
            logger.error(f"❌ 정보 추출 타임아웃")
            return None
        except Exception as e:
            logger.error(f"❌ 정보 추출 실패: {e}")
            return None

    async def _send_error_message(self, message):
        """에러 메시지 전송 (별도 태스크)"""
        try:
            await self.bot.get_channel(config.CHANNEL_ID).send(message, delete_after=5)
        except:
            pass

    async def _ensure_voice_connection(self, voice_channel):
        """음성 채널 연결"""
        try:
            if not self.vc:
                self.vc = await voice_channel.connect()
                logger.info(f"🔊 음성 채널 연결: {voice_channel.name}")
            elif self.vc.channel != voice_channel:
                await self.vc.move_to(voice_channel)
        except Exception as e:
            logger.error(f"❌ 음성 채널 연결 실패: {e}")

    async def update_ui(self):
        """UI 업데이트 - 재생과 독립적"""
        try:
            if not self.current:
                embed = discord.Embed(
                    title="🎵 음악 플레이어",
                    description="제목을 입력하여 음악을 재생하세요",
                    color=0x00ff00
                )
                embed.add_field(name="📋 대기중인 곡", value=f"{len(self.queue)}개", inline=True)
                
                # 로딩 중인 곡 표시
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
                
                # 썸네일
                if track.get("id"):
                    embed.set_thumbnail(url=f"https://img.youtube.com/vi/{track['id']}/hqdefault.jpg")
            
            await self.message.edit(embed=embed, view=MusicView(self))
            
        except Exception as e:
            logger.error(f"❌ UI 업데이트 실패: {e}")

    @tasks.loop(seconds=1.0)  # 1초로 조정 (안정성 향상)
    async def auto_play(self):
        """자동 재생 루프 - 검색과 완전 분리"""
        try:
            if self.vc and not self.vc.is_playing():
                if self.queue:
                    # 로딩 중인 트랙 건너뛰기 (논블로킹)
                    ready_tracks = [t for t in self.queue if not t.get("loading")]
                    if not ready_tracks:
                        return  # 준비된 트랙이 없으면 대기
                    
                    # 첫 번째 준비된 트랙 재생
                    track = ready_tracks[0]
                    self.queue.remove(track)
                    
                    # 스트림 URL 확인
                    if not track.get("stream_url"):
                        logger.error(f"❌ 스트림 URL 없음: {track['title']}")
                        await self.update_ui()
                        return
                    
                    # 재생 시작
                    try:
                        source = discord.FFmpegPCMAudio(
                            track["stream_url"],
                            **FFMPEG_OPTIONS
                        )
                        
                        self.current = [track]
                        self.vc.play(source)
                        await self.update_ui()
                        
                        logger.info(f"▶️ 재생 시작: {track['title'][:30]}")
                        
                    except Exception as e:
                        logger.error(f"❌ 재생 실패: {e}")
                        await self.update_ui()
                        return
                        
                elif self.current:
                    self.current = []
                    await self.update_ui()
                    logger.info("⏹️ 재생 완료")
                
                # 자동 종료 (5분)
                if (self.vc and self.vc.channel and 
                    len(self.vc.channel.members) == 1 and 
                    not self.queue and not self.current):
                    
                    await asyncio.sleep(300)
                    if (self.vc and self.vc.channel and 
                        len(self.vc.channel.members) == 1):
                        await self.stop()
                    
        except Exception as e:
            logger.error(f"❌ 재생 루프 오류: {e}")
            await self.update_ui()

    async def stop(self):
        """재생 중지"""
        try:
            self.queue.clear()
            self.current = []
            
            if self.vc:
                if self.vc.is_playing():
                    self.vc.stop()
                await self.vc.disconnect()
                self.vc = None
                
            await self.update_ui()
            logger.info("🛑 플레이어 중지")
            
        except Exception as e:
            logger.error(f"❌ 중지 오류: {e}")

    async def skip(self):
        """건너뛰기"""
        if self.vc and self.vc.is_playing():
            self.vc.stop()
            logger.info("⏭️ 곡 건너뛰기")
            return True
        return False

    def get_queue_info(self):
        """큐 정보"""
        return {
            'current': self.current[0] if self.current else None,
            'queue_length': len(self.queue),
            'total_duration': sum(track.get('duration', 0) for track in self.queue),
            'is_playing': self.vc.is_playing() if self.vc else False
        }

    # 캐시 관련 함수들은 호환성을 위해 유지 (아무것도 하지 않음)
    def clear_cache(self):
        """캐시 정리 (캐시 없음)"""
        logger.info("🗑️ 캐시 기능 비활성화됨")

    async def get_cache_stats(self):
        """캐시 통계 (캐시 없음)"""
        return {
            'total_items': 0,
            'file_size_kb': 0,
            'file_exists': False,
            'total_plays': 0,
            'oldest_cache': None,
            'cache_disabled': True
        }

    async def shutdown_handler(self):
        """정상 종료 처리"""
        logger.info("🔄 봇 종료 준비 중...")
        await self.cleanup()
        logger.info("🧹 리소스 정리 완료")