import discord
import config
import asyncio
import aiohttp
import logging
import json
import os
from datetime import datetime, timedelta
from discord.ext import tasks
from yt_dlp import YoutubeDL
from functools import lru_cache
from ui.controls import MusicView

logger = logging.getLogger(__name__)

FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
        "-headers 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)'"
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
    
    # 수동 테스트에서 성공한 것과 동일한 옵션들
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    },
    
    # 추가 성공 옵션들
    'extractor_args': {
        'youtube': {
            'skip': ['hls'],
            'player_skip': ['configs'],
            'player_client': ['tv', 'ios'],  # 다중 클라이언트 시도
        }
    },
    
    'geo_bypass': True,
    'age_limit': None,
    'socket_timeout': 30,
    'retries': 2,
}

# 캐시 파일 경로
CACHE_FILE = "music_cache.json"

class Player:
    def __init__(self, bot):
        self.bot = bot
        self.vc = None
        self.queue = []
        self.current = []
        self.channel = None
        self.message = None
        self.session = None
        self._cache = {}  # 통합 캐시

    async def initialize(self):
        """플레이어 초기화"""
        try:
            self.channel = self.bot.get_channel(config.CHANNEL_ID)
            self.message = await self.channel.fetch_message(config.MSG_ID)
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8)
            )
            
            # 캐시 로드
            await self.load_cache()
            
            self.auto_play.start()
            logging.basicConfig(level=logging.INFO)
            logger.info("✅ Player 초기화 완료")
        except Exception as e:
            logger.error(f"❌ Player 초기화 실패: {e}")
            raise

    async def load_cache(self):
        """캐시 파일 로드 - 영구 보관 버전"""
        try:
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                
                self._cache = cache_data
                logger.info(f"📁 캐시 로드 완료: {len(self._cache)}개 항목 (영구 보관)")
            else:
                self._cache = {}
                logger.info("📁 새로운 캐시 파일 생성 (영구 보관)")
                
        except Exception as e:
            logger.error(f"❌ 캐시 로드 실패: {e}")
            self._cache = {}

    async def save_cache(self):
        """캐시 파일 저장"""
        try:
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
            logger.info(f"💾 캐시 저장 완료: {len(self._cache)}개 항목")
        except Exception as e:
            logger.error(f"❌ 캐시 저장 실패: {e}")

    def get_cache_key(self, query):
        """검색어 기반 캐시 키 (사용 안함, URL 기반으로 변경됨)"""
        return query.lower().strip().replace(' ', '_')

    async def cleanup(self):
        """리소스 정리"""
        # 캐시 저장
        await self.save_cache()
        
        if self.session:
            await self.session.close()
        if self.vc:
            await self.vc.disconnect()

    async def handle_message(self, message):
        """메시지 처리 - 백그라운드 검색으로 재생 끊김 방지"""
        if message.author == self.bot.user or message.channel.id != config.CHANNEL_ID:
            return

        # 안전한 메시지 삭제
        try:
            await message.delete()
        except discord.errors.NotFound:
            pass
        except discord.errors.Forbidden:
            logger.warning("메시지 삭제 권한이 없습니다")
        except Exception as e:
            logger.warning(f"메시지 삭제 실패: {e}")
        
        # 음성 채널 확인
        if not message.author.voice:
            try:
                error_msg = await message.channel.send("❌ 음성 채널에 먼저 접속해주세요!", delete_after=3)
            except:
                pass
            return
        
        query = message.content.strip()
        
        # 🚀 백그라운드에서 검색 처리 (재생 끊김 방지)
        asyncio.create_task(self._background_search_and_add(query, message.author))

    async def _background_search_and_add(self, query, author):
        """백그라운드에서 검색 및 큐 추가 - 재생과 분리"""
        try:
            # 1단계: 즉시 임시 트랙 추가 (UI 즉시 업데이트)
            temp_track = {
                "title": f"🔍 {query[:25]}... 검색중",
                "duration": 0,
                "user": f"<@{author.id}>",
                "id": "",
                "video_url": "",
                "stream_url": None,
                "loading": True
            }
            
            self.queue.append(temp_track)
            await self.update_ui()  # 즉시 UI 업데이트
            
            # 2단계: 백그라운드에서 검색 (재생과 별도 스레드)
            loop = asyncio.get_event_loop()
            video_url, track_info = await loop.run_in_executor(
                None, 
                self._sync_search_and_extract, 
                query
            )
            
            if not video_url or not track_info:
                # 실패시 임시 트랙 제거
                if temp_track in self.queue:
                    self.queue.remove(temp_track)
                
                try:
                    error_msg = await self.bot.get_channel(config.CHANNEL_ID).send(
                        f"❌ '{query}' 를 찾을 수 없습니다.", delete_after=5
                    )
                except:
                    pass
                await self.update_ui()
                return
            
            # 3단계: 실제 트랙으로 교체
            real_track = {
                "title": track_info["title"][:95],
                "duration": int(track_info.get("duration", 0)),
                "user": f"<@{author.id}>",
                "id": track_info.get("id", ""),
                "video_url": video_url,
                "stream_url": track_info.get("url"),
                "uploader": track_info.get("uploader", "Unknown")
            }
            
            # 임시 트랙을 실제 트랙으로 교체
            if temp_track in self.queue:
                idx = self.queue.index(temp_track)
                self.queue[idx] = real_track
            else:
                self.queue.append(real_track)
            
            # UI 업데이트 및 음성 연결
            await self.update_ui()
            await self._ensure_voice_connection(author.voice.channel)
            
            logger.info(f"⚡ 백그라운드 추가 완료: {real_track['title']}")
            
        except Exception as e:
            # 오류시 임시 트랙 제거
            if temp_track in self.queue:
                self.queue.remove(temp_track)
            
            logger.error(f"❌ 백그라운드 처리 오류: {e}")
            try:
                error_msg = await self.bot.get_channel(config.CHANNEL_ID).send(
                    f"❌ 검색 오류가 발생했습니다", delete_after=3
                )
            except:
                pass
            await self.update_ui()

    def _sync_search_and_extract(self, query):
        """동기식 검색 및 추출 (별도 스레드용)"""
        try:
            # 새로운 이벤트 루프와 세션 생성
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            async def search_with_new_session():
                # 새로운 aiohttp 세션 생성
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as session:
                    # 임시로 세션 교체
                    original_session = self.session
                    self.session = session
                    
                    try:
                        result = await self.fast_search_and_extract(query)
                        return result
                    finally:
                        # 원래 세션 복구
                        self.session = original_session
            
            try:
                return loop.run_until_complete(search_with_new_session())
            finally:
                loop.close()
                
        except Exception as e:
            logger.error(f"❌ 동기식 검색 실패: {e}")
            return None, None

    async def fast_search_and_extract(self, query):
        """초고속 검색 및 정보 추출 - URL 기반 캐시"""
        try:
            # 1단계: URL 획득 (빠름)
            if "youtube.com/watch" in query or "youtu.be/" in query:
                video_url = query
            else:
                video_url = await self.lightning_search(query)
                if not video_url:
                    return None, None
            
            # 2단계: URL을 캐시 키로 사용하여 정보 확인
            cache_key = self.get_url_cache_key(video_url)
            
            if cache_key in self._cache:
                cached_data = self._cache[cache_key]
                # 재생 횟수 증가
                cached_data['play_count'] = cached_data.get('play_count', 0) + 1
                cached_data['last_played'] = datetime.now().isoformat()
                logger.info(f"⚡ URL 캐시 사용 ({cached_data['play_count']}회째): {cached_data['track_info']['title'][:30]}")
                return video_url, cached_data['track_info']
            
            # 3단계: 캐시에 없으면 정보 추출 (느림)
            logger.info(f"🔄 새로운 URL 정보 추출: {video_url}")
            track_info = await self.lightning_extract(video_url)
            if not track_info:
                return None, None
            
            # 4단계: URL 기반으로 캐시 저장 (영구 보관)
            cache_data = {
                'track_info': track_info,
                'cached_at': datetime.now().isoformat(),
                'original_query': query,  # 디버깅용
                'video_url': video_url,
                'play_count': 1  # 재생 횟수 추가
            }
            
            self._cache[cache_key] = cache_data
            
            # 주기적 저장
            if len(self._cache) % 5 == 0:
                await self.save_cache()
            
            logger.info(f"📦 URL 캐시 저장: {track_info['title'][:30]}")
            return video_url, track_info
            
        except Exception as e:
            logger.error(f"❌ 빠른 추출 실패: {e}")
            return None, None

    def get_url_cache_key(self, video_url):
        """URL에서 캐시 키 추출"""
        try:
            # YouTube URL에서 video ID 추출
            if "youtube.com/watch?v=" in video_url:
                video_id = video_url.split("watch?v=")[1].split("&")[0]
            elif "youtu.be/" in video_url:
                video_id = video_url.split("youtu.be/")[1].split("?")[0]
            else:
                # 기타 URL은 해시값 사용
                import hashlib
                video_id = hashlib.md5(video_url.encode()).hexdigest()[:11]
            
            return f"url_{video_id}"
            
        except Exception:
            # 실패시 전체 URL 해시
            import hashlib
            return f"url_{hashlib.md5(video_url.encode()).hexdigest()[:11]}"

    async def lightning_extract(self, url):
        """초고속 정보 추출 - 성공한 옵션 사용"""
        loop = asyncio.get_event_loop()
        
        try:
            # 성공한 수동 테스트와 동일한 옵션 사용
            ydl_opts = {
                'format': 'bestaudio/best',
                'quiet': True,
                'no_warnings': True,
                'extractaudio': True,
                'noplaylist': True,
                'nocheckcertificate': True,
                'ignoreerrors': False,
                'extract_flat': False,
                'skip_download': True,
                'cookiefile': 'cookies.txt',  # 동일한 쿠키 파일
            }
            
            with YoutubeDL(ydl_opts) as ydl:
                # 타임아웃을 15초로 늘림
                info = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False)),
                    timeout=15.0
                )
                
                if not info or not info.get('url'):
                    logger.error(f"❌ 스트림 URL 없음: {url}")
                    return None
                
                logger.info(f"⚡ 빠른 추출 성공: {info.get('title', 'Unknown')[:30]}")
                return {
                    'title': info.get('title', 'Unknown Title'),
                    'duration': info.get('duration', 0),
                    'id': info.get('id', ''),
                    'uploader': info.get('uploader', 'Unknown'),
                    'url': info.get('url'),
                }
                
        except asyncio.TimeoutError:
            logger.error(f"❌ 추출 타임아웃: {url}")
            return None
        except Exception as e:
            logger.error(f"❌ 추출 실패: {e}")
            return None

    async def lightning_search(self, query):
        """초고속 검색 - 첫 번째 결과만 사용"""
        try:
            # 스마트 검색어 생성
            if len(query.split()) <= 2:
                search_query = f"{query} 가사"
            else:
                search_query = query
            
            params = {
                "part": "snippet",
                "q": search_query,
                "type": "video",
                "key": config.YOUTUBE_API_KEY,
                "maxResults": 1,  # 첫 번째 결과만
                "regionCode": "KR",
                "videoCategoryId": "10"
            }
            
            async with self.session.get(
                "https://www.googleapis.com/youtube/v3/search", 
                params=params
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    items = data.get("items", [])
                    
                    if items:
                        video_url = f"https://www.youtube.com/watch?v={items[0]['id']['videoId']}"
                        logger.info(f"⚡ 빠른 검색 성공: {items[0]['snippet']['title'][:30]}")
                        return video_url
                else:
                    logger.error(f"❌ YouTube API 오류: {response.status}")
                    error_text = await response.text()
                    logger.error(f"❌ API 응답: {error_text[:200]}")
            
            return None
            
        except Exception as e:
            logger.error(f"❌ 빠른 검색 실패: {e}")
            return None

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
        """UI 업데이트"""
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

    @tasks.loop(seconds=0.5)  # 0.5초로 단축 (더 빠른 반응)
    async def auto_play(self):
        """자동 재생 루프 - 검색 버벅임 방지"""
        try:
            if self.vc and not self.vc.is_playing():
                if self.queue:
                    # 로딩 중인 트랙은 건너뛰기
                    while self.queue and self.queue[0].get("loading"):
                        await asyncio.sleep(0.1)  # 짧은 대기
                        continue
                    
                    if not self.queue:  # 큐가 비었으면 종료
                        return
                    
                    track = self.queue.pop(0)
                    
                    # 스트림 URL 확인
                    if not track.get("stream_url"):
                        logger.error(f"❌ 스트림 URL 없음: {track['title']}")
                        await self.update_ui()
                        return
                    
                    # 재생 시작
                    try:
                        source = discord.FFmpegPCMAudio(
                            track["stream_url"], 
                            executable="/usr/bin/ffmpeg",
                            **FFMPEG_OPTIONS
                        )
                        
                        self.current = [track]
                        self.vc.play(source)
                        await self.update_ui()
                        
                        logger.info(f"▶️ 재생 시작: {track['title']}")
                        
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
            logger.error(f"❌ auto_play 오류: {e}")

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

    def clear_cache(self):
        """캐시 정리"""
        self._cache.clear()
        # 파일도 삭제
        try:
            if os.path.exists(CACHE_FILE):
                os.remove(CACHE_FILE)
            logger.info("🧹 캐시 파일 삭제 완료")
        except Exception as e:
            logger.error(f"❌ 캐시 파일 삭제 실패: {e}")

    async def get_cache_stats(self):
        """캐시 통계 - 영구 보관 버전"""
        total_items = len(self._cache)
        file_size = 0
        total_plays = 0
        oldest_cache = None
        
        try:
            if os.path.exists(CACHE_FILE):
                file_size = os.path.getsize(CACHE_FILE)
            
            # 통계 계산
            for cache_data in self._cache.values():
                total_plays += cache_data.get('play_count', 1)
                cache_time = cache_data.get('cached_at')
                if cache_time:
                    if not oldest_cache or cache_time < oldest_cache:
                        oldest_cache = cache_time
        except:
            pass
        
        return {
            'total_items': total_items,
            'file_size_kb': round(file_size / 1024, 2),
            'file_exists': os.path.exists(CACHE_FILE),
            'total_plays': total_plays,
            'oldest_cache': oldest_cache,
            'permanent_storage': True  # 영구 보관 표시
        }

    # 종료 시 리소스 정리를 위한 함수들 추가
    async def shutdown_handler(self):
        """정상 종료 처리"""
        logger.info("🔄 봇 종료 준비 중...")
        await self.save_cache()
        logger.info("💾 캐시 저장 완료")
        await self.cleanup()
        logger.info("🧹 리소스 정리 완료")