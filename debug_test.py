import asyncio
import aiohttp
import config
from yt_dlp import YoutubeDL
import logging

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_search(query):
    """검색 테스트"""
    print(f"\n🔍 '{query}' 검색 테스트 시작...")
    
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
                
                print(f"📋 {len(items)}개 검색 결과:")
                
                for i, item in enumerate(items):
                    title = item['snippet']['title']
                    video_id = item['id']['videoId']
                    video_url = f"https://www.youtube.com/watch?v={video_id}"
                    
                    print(f"  {i+1}. {title}")
                    print(f"     URL: {video_url}")
                    
                    # 재생 가능성 테스트
                    playable = await test_video_playable(video_url)
                    print(f"     재생 가능: {'✅' if playable else '❌'}")
                    print()
                    
                    if playable:
                        print(f"🎯 첫 번째 재생 가능한 영상: {title}")
                        return video_url
                        
            else:
                print(f"❌ API 오류: {response.status}")
                
    except Exception as e:
        print(f"❌ 검색 오류: {e}")
    finally:
        await session.close()
    
    return None

async def test_video_playable(url):
    """비디오 재생 가능성 테스트"""
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'format': 'worst',
            'ignoreerrors': True,
            'extract_flat': False
        }
        
        # 쿠키 사용 (있는 경우)
        try:
            with open(config.COOKIES_FILE, 'r'):
                ydl_opts['cookiefile'] = config.COOKIES_FILE
        except FileNotFoundError:
            pass
        
        loop = asyncio.get_event_loop()
        
        with YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False)),
                timeout=5.0
            )
            
            if not info:
                return False
            
            title = info.get('title')
            duration = info.get('duration')
            
            if not title:
                return False
            
            # 너무 긴 영상 제외 (3시간 이상)
            if duration and duration > 10800:
                return False
            
            # 라이브 스트림 제외
            if info.get('is_live'):
                return False
            
            return True
            
    except Exception as e:
        print(f"    ⚠️ 확인 오류: {str(e)[:50]}")
        return False

async def test_extract_info(url):
    """정보 추출 테스트"""
    print(f"\n📋 '{url}' 정보 추출 테스트...")
    
    ydl_opts = {
        'format': 'bestaudio[ext=webm]/bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'extractaudio': True,
        'audioformat': 'webm',
        'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'no_color': True,
        'extract_flat': False,
        'writethumbnail': False,
        'writeinfojson': False,
    }
    
    # 쿠키 사용 (있는 경우)
    try:
        with open(config.COOKIES_FILE, 'r'):
            ydl_opts['cookiefile'] = config.COOKIES_FILE
            print("🍪 쿠키 파일 사용")
    except FileNotFoundError:
        print("🍪 쿠키 파일 없음")
    
    try:
        loop = asyncio.get_event_loop()
        
        with YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False)),
                timeout=15.0
            )
            
            if info:
                print(f"✅ 제목: {info.get('title', 'Unknown')}")
                print(f"✅ 길이: {info.get('duration', 0)}초")
                print(f"✅ 업로더: {info.get('uploader', 'Unknown')}")
                print(f"✅ 스트림 URL: {'있음' if info.get('url') else '없음'}")
                return True
            else:
                print("❌ 정보 없음")
                return False
                
    except Exception as e:
        print(f"❌ 오류: {e}")
        return False

async def main():
    """메인 테스트"""
    print("🧪 YouTube 검색 및 재생 테스트 시작\n")
    
    # 테스트할 검색어들
    test_queries = [
        "BIG BIRD",
        "BIG BIRD 가사",
        "빅버드",
        "세서미 스트리트"
    ]
    
    for query in test_queries:
        video_url = await test_search(query)
        
        if video_url:
            success = await test_extract_info(video_url)
            if success:
                print(f"🎉 '{query}' 테스트 성공!")
                break
        else:
            print(f"❌ '{query}' 테스트 실패")
        
        print("-" * 50)

if __name__ == "__main__":
    asyncio.run(main())