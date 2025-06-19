import discord
from discord.ext import commands
import config
from music.player import Player
import signal
import asyncio

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix="/", intents=intents)
player = Player(bot)

@bot.event
async def on_ready():
    await player.initialize()
    print(f"✅ Logged in as {bot.user}")

@bot.event
async def on_message(message):
    await player.handle_message(message)

# 종료 시 리소스 정리 (추가된 부분)
async def cleanup():
    print("🧹 리소스 정리 중...")
    await player.cleanup()
    await bot.close()

def signal_handler(sig, frame):
    print("⏹️ 종료 신호 받음")
    asyncio.create_task(cleanup())

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

try:
    bot.run(config.BOT_TOKEN)
except KeyboardInterrupt:
    print("👋 봇 종료")
finally:
    asyncio.run(cleanup())