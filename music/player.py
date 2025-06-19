import discord
import config
import asyncio
import aiohttp
import logging
from discord.ext import tasks
from yt_dlp import YoutubeDL
from datetime import timedelta
from functools import lru_cache
from ui.controls import MusicView

logger = logging.getLogger(__name__)

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn"
}

class Player:
    def __init__(self, bot):
        self.bot = bot
        self.vc = None
        self.queue = []
        self.current = []
        self.channel = None
        self.message = None
        self.session = None  # aiohttp 세션
        self._search_cache = {}  # 간단한 메모리 캐시

    async def initialize(self):
        """플레이어 초기화"""
        try:
            self.channel = self.bot.get_channel(config.CHANNEL_ID)
            self.message = await self.channel.fetch_message(config.MSG_ID)
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            )
            self.auto_play.start()
            logger.info("✅ Player 초기화 완료")
        except Exception as e:
            logger.error(f"❌ Player 초기화 실패: {e}")
            raise

    async def cleanup(self):
        """리소스 정리"""
        if self.session:
            await self.session.close()
        if self.vc:
            await self.vc.disconnect()

    async def handle_message(self, message):
        """메시지 처리 - 성능 최적화된 버전"""
        if message.author == self.bot.user or message.channel.id != config.CHANNEL_ID:
            return

        # 1단계: 즉시 반응 (0.1초)
        await message.delete()
        
        # 음성 채널 연결 확인
        if not message.author.voice:
            await message.channel.send("❌ 음성 채널에 먼저 접속해주세요!", delete_after=3)
            return
        
        # 로딩 메시지 표시
        loading_msg = await message.channel.send("🔍 **검색 중...**")
        
        try:
            # 2단계: 빠른 검색 (0.5초 목표)
            query = message.content.strip()
            
            if "https://" not in query:
                # 스마트 검색어 처리
                search_query = self._optimize_search_query(query)
                video_url = await self.search_youtube_async(search_query)
                
                if not video_url:
                    await loading_msg.edit(content="❌ 검색 결과가 없습니다.")
                    await asyncio.sleep(2)
                    await loading_msg.delete()
                    return
            else:
                video_url = query
            
            # 상태 업데이트
            await loading_msg.edit(content="⏳ **정보 추출 중...**")
            
            # 3단계: 기본 정보 추출 (1초 목표)
            basic_info = await self.extract_basic_info(video_url)
            
            if not basic_info:
                await loading_msg.edit(content="❌ 재생할 수 없는 영상입니다.")
                await asyncio.sleep(2)
                await loading_msg.delete()
                return
            
            # 4단계: 큐에 추가 (즉시)
            track_data = {
                "title": basic_info["title"][:95],
                "duration": int(basic_info.get("duration", 0)),
                "user": f"<@{message.author.id}>",
                "id": basic_info["id"],
                "video_url": video_url,
                "stream_url": None,  # 나중에 추출
                "source": None       # 재생 직전에 생성
            }
            
            self.queue.append(track_data)
            
            # 로딩 메시지 삭제
            await loading_msg.delete()
            
            # UI 업데이트
            await self.update_ui()
            
            # 음성 채널 연결 (백그라운드)
            await self._ensure_voice_connection(message.author.voice.channel)
            
            logger.info(f"✅ 곡 추가: {track_data['title']}")
            
        except Exception as e:
            logger.error(f"❌ 메시지 처리 오류: {e}")
            await loading_msg.edit(content=f"❌ 오류 발생: 다시 시도해주세요")
            await asyncio.sleep(3)
            await loading_msg.delete()

    def _optimize_search_query(self, query):
        """검색어 최적화"""
        query_lower = query.lower()
        
        # 이미 완성된 제목이면 가사 추가 안함
        skip_keywords = ['official', 'mv', 'music video', '가사', 'lyrics', 'cover']
        if any(word in query_lower for word in skip_keywords):
            return query
        
        # 짧은 검색어에만 가사 추가
        if len(query) < 20:
            return f"{query} 가사"
        
        return query

    async def search_youtube_async(self, query):
        """비동기 YouTube 검색 with 캐싱"""
        # 캐시 확인
        if query in self._search_cache:
            logger.info(f"🔍 캐시에서 검색 결과 사용: {query}")
            return self._search_cache[query]
        
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "key": config.YOUTUBE_API_KEY,
            "maxResults": 1,
            "regionCode": "KR",  # 한국 지역 우선
            "relevanceLanguage": "ko"  # 한국어 우선
        }
        
        try:
            async with self.session.get(
                "https://www.googleapis.com/youtube/v3/search", 
                params=params
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    items = data.get("items")
                    if items:
                        video_url = f"https://www.youtube.com/watch?v={items[0]['id']['videoId']}"
                        
                        # 캐시에 저장 (최대 100개까지)
                        if len(self._search_cache) < 100:
                            self._search_cache[query] = video_url
                        
                        logger.info(f"🔍 검색 성공: {query}")
                        return video_url
                else:
                    logger.warning(f"⚠️ YouTube API 응답 오류: {response.status}")
                    
        except asyncio.TimeoutError:
            logger.error(f"❌ YouTube 검색 타임아웃: {query}")
        except Exception as e:
            logger.error(f"❌ YouTube 검색 오류: {e}")
        
        return None

    async def extract_basic_info(self, url):
        """기본 정보만 빠르게 추출"""
        loop = asyncio.get_event_loop()
        
        try:
            # 최소한의 옵션으로 빠르게 처리
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'skip_download': True,
                'format': 'worst',  # 빠른 처리를 위해
                'ignoreerrors': True,
            }
            
            with YoutubeDL(ydl_opts) as ydl:
                # 타임아웃 설정으로 최대 3초만 대기
                info = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False)),
                    timeout=3.0
                )
                
                return {
                    'title': info.get('title', 'Unknown Title'),
                    'duration': info.get('duration', 0),
                    'id': info.get('id', ''),
                    'uploader': info.get('uploader', ''),
                    'view_count': info.get('view_count', 0)
                }
                
        except asyncio.TimeoutError:
            logger.error(f"❌ 정보 추출 타임아웃: {url}")
            return None
        except Exception as e:
            logger.error(f"❌ 기본 정보 추출 실패: {e}")
            return None

    async def extract_stream_url(self, video_url):
        """재생 직전에 스트림 URL 추출"""
        loop = asyncio.get_event_loop()
        
        try:
            ydl_opts = {
                'quiet': True,
                'format': 'bestaudio/best',
                'extractaudio': True,
                'noplaylist': True,
                'cookies': config.COOKIES_FILE,
                'cachedir': False,  # 캐시 비활성화로 속도 향상
            }
            
            with YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, 
                        lambda: ydl.extract_info(video_url, download=False)
                    ),
                    timeout=10.0  # 스트림 URL은 좀 더 여유있게
                )
                return info.get('url')
                
        except asyncio.TimeoutError:
            logger.error(f"❌ 스트림 URL 추출 타임아웃: {video_url}")
            return None
        except Exception as e:
            logger.error(f"❌ 스트림 URL 추출 실패: {e}")
            return None

    async def _ensure_voice_connection(self, voice_channel):
        """음성 채널 연결 보장"""
        try:
            if not self.vc:
                self.vc = await voice_channel.connect()
                logger.info(f"🔊 음성 채널 연결: {voice_channel.name}")
            elif self.vc.channel != voice_channel:
                await self.vc.move_to(voice_channel)
                logger.info(f"🔄 음성 채널 이동: {voice_channel.name}")
        except Exception as e:
            logger.error(f"❌ 음성 채널 연결 실패: {e}")

    async def update_ui(self):
        """UI 업데이트 - 에러 핸들링 강화"""
        try:
            if not self.current:
                embed = discord.Embed(
                    title="🎵 음악 플레이어",
                    description="제목을 입력하여 음악을 재생하세요",
                    color=0x00ff00
                )
                embed.add_field(name="대기중인 곡", value=f"{len(self.queue)}개", inline=True)
            else:
                track = self.current[0]
                embed = discord.Embed(
                    title=f"🎵 {track['title']}", 
                    color=0x00ff00
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
                
                # 썸네일 설정
                if track.get("id"):
                    embed.set_thumbnail(url=f"https://img.youtube.com/vi/{track['id']}/0.jpg")
            
            await self.message.edit(embed=embed, view=MusicView(self))
            
        except Exception as e:
            logger.error(f"❌ UI 업데이트 실패: {e}")

    @tasks.loop(seconds=1)
    async def auto_play(self):
        """자동 재생 루프 - 지연 로딩 적용"""
        try:
            if self.vc and not self.vc.is_playing():
                if self.queue:
                    track = self.queue.pop(0)
                    
                    # 스트림 URL이 없으면 추출
                    if not track.get("stream_url"):
                        logger.info(f"🔄 스트림 URL 추출 시작: {track['title']}")
                        track["stream_url"] = await self.extract_stream_url(track["video_url"])
                        
                        if not track["stream_url"]:
                            logger.error(f"❌ 스트림 URL 추출 실패, 다음 곡으로: {track['title']}")
                            await self.update_ui()
                            return
                    
                    # 오디오 소스 생성 (재생 직전)
                    try:
                        track["source"] = discord.FFmpegPCMAudio(
                            track["stream_url"], 
                            **FFMPEG_OPTIONS
                        )
                        
                        self.current = [track]
                        self.vc.play(track["source"])
                        await self.update_ui()
                        
                        logger.info(f"▶️ 재생 시작: {track['title']}")
                        
                    except Exception as e:
                        logger.error(f"❌ 오디오 소스 생성 실패: {e}")
                        # 실패시 다음 곡으로
                        await self.update_ui()
                        return
                        
                elif self.current:
                    self.current = []
                    await self.update_ui()
                    logger.info("⏹️ 재생 완료")
                
                # 혼자 있으면 나가기
                if self.vc and self.vc.channel and len(self.vc.channel.members) == 1:
                    logger.info("👤 사용자가 없어서 음성 채널에서 나갑니다")
                    await self.stop()
                    
        except Exception as e:
            logger.error(f"❌ auto_play 루프 오류: {e}")

    async def stop(self):
        """재생 중지 및 정리"""
        try:
            self.queue.clear()
            self.current = []
            
            if self.vc:
                await self.vc.disconnect()
                self.vc = None
                
            await self.update_ui()
            logger.info("🛑 플레이어 중지")
            
        except Exception as e:
            logger.error(f"❌ 플레이어 중지 오류: {e}")

    async def skip(self):
        """다음 곡으로 건너뛰기"""
        if self.vc and self.vc.is_playing():
            self.vc.stop()
            logger.info("⏭️ 곡 건너뛰기")
            return True
        return False

    def get_queue_info(self):
        """큐 정보 반환"""
        return {
            'current': self.current[0] if self.current else None,
            'queue_length': len(self.queue),
            'total_duration': sum(track.get('duration', 0) for track in self.queue),
            'is_playing': self.vc.is_playing() if self.vc else False
        }

    async def preload_next_track(self):
        """다음 트랙 미리 로드 (선택적 최적화)"""
        if self.queue and not self.queue[0].get("stream_url"):
            try:
                next_track = self.queue[0]
                logger.info(f"🔄 다음 곡 미리 로드: {next_track['title']}")
                next_track["stream_url"] = await self.extract_stream_url(next_track["video_url"])
                if next_track["stream_url"]:
                    logger.info(f"✅ 미리 로드 완료: {next_track['title']}")
            except Exception as e:
                logger.error(f"❌ 미리 로드 실패: {e}")

    def clear_cache(self):
        """검색 캐시 정리"""
        self._search_cache.clear()
        logger.info("🧹 검색 캐시 정리 완료")