import discord
import config
import asyncio
from discord.ext import tasks
from yt_dlp import YoutubeDL
from datetime import timedelta
import requests
from ui.controls import MusicView

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

    async def initialize(self):
        self.channel = self.bot.get_channel(config.CHANNEL_ID)
        self.message = await self.channel.fetch_message(config.MSG_ID)
        self.auto_play.start()

    async def handle_message(self, message):
        if message.author == self.bot.user or message.channel.id != config.CHANNEL_ID:
            return

        try:
            self.vc = await message.author.voice.channel.connect()
        except:
            try:
                await self.vc.move_to(message.author.voice.channel)
            except:
                await message.channel.send("채널에 유저가 접속해있지 않습니다", delete_after=3)
                return

        query = message.content
        if "https://" not in query:
            query = self.search_youtube(query + " 가사")
            if not query:
                await message.channel.send("유튜브 검색 결과가 없습니다.", delete_after=5)
                return

        info = await self.extract_info(query)
        if not info:
            await message.channel.send("⚠️ 이 영상은 재생할 수 없습니다. 다른 곡을 시도해 주세요.", delete_after=5)
            return

        self.queue.append({
            "source": discord.FFmpegPCMAudio(info["url"], **FFMPEG_OPTIONS),
            "title": info["title"][:95],
            "duration": int(info.get("duration", 0)),
            "user": f"<@{message.author.id}>",
            "id": info["id"]
        })

        await message.delete()
        await self.update_ui()

    def search_youtube(self, query):
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "key": config.YOUTUBE_API_KEY,
            "maxResults": 1
        }
        response = requests.get("https://www.googleapis.com/youtube/v3/search", params=params)
        if response.status_code == 200:
            items = response.json().get("items")
            if items:
                return f"https://www.youtube.com/watch?v={items[0]['id']['videoId']}"
        return None

    async def extract_info(self, url):
        loop = asyncio.get_event_loop()
        try:
            ydl_opts = {
                'quiet': True,
                'format': 'bestaudio/best',
                'extractaudio': True,
                'noplaylist': True,
                'forcejson': True,
                "cookies": config.COOKIES_FILE
            }
            with YoutubeDL(ydl_opts) as ydl:
                return await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
        except Exception as e:
            print(f"[❌ ERROR] 영상 정보 추출 실패: {e}")
            return None

    async def update_ui(self):
        embed = discord.Embed(title="제목을 입력하세요") if not self.current else discord.Embed(title=self.current[0]["title"])
        if self.current:
            embed.add_field(name="노래길이", value=str(timedelta(seconds=self.current[0]["duration"])), inline=True)
            embed.add_field(name="대기중인 곡", value=f"{len(self.queue)}개", inline=True)
            embed.add_field(name="요청자", value=self.current[0]["user"], inline=True)
            embed.set_thumbnail(url=f"https://img.youtube.com/vi/{self.current[0]['id']}/0.jpg")
        await self.message.edit(embed=embed, view=MusicView(self))

    @tasks.loop(seconds=1)
    async def auto_play(self):
        if self.vc and not self.vc.is_playing():
            if self.queue:
                self.current.insert(0, self.queue.pop(0))
                self.vc.play(self.current[0]["source"])
                await self.update_ui()
            elif self.current:
                self.current = []
                await self.update_ui()
            if self.vc.channel and len(self.vc.channel.members) == 1:
                await self.stop()

    async def stop(self):
        self.queue.clear()
        if self.vc:
            await self.vc.disconnect()
