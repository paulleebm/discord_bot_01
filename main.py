import discord
from discord.ext import commands
import config
from music.player import Player
import signal
import asyncio
import logging
from datetime import datetime

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix="/", intents=intents)
player = Player(bot)

@bot.event
async def on_ready():
    await player.initialize()
    
    # 슬래시 커맨드 동기화를 여기서 실행
    try:
        synced = await bot.tree.sync()
        logger.info(f"🔄 슬래시 커맨드 동기화 완료: {len(synced)}개")
    except Exception as e:
        logger.error(f"❌ 슬래시 커맨드 동기화 실패: {e}")
    
    print(f"✅ Logged in as {bot.user}")

@bot.event
async def on_message(message):
    await player.handle_message(message)

# 간단한 상태 확인 슬래시 커맨드
@bot.tree.command(name="status", description="플레이어 상태 확인")
async def status(interaction: discord.Interaction):
    """플레이어 상태 확인"""
    info = player.get_queue_info()
    
    embed = discord.Embed(title="🎵 플레이어 상태", color=0x00ff00)
    embed.add_field(name="🎵 현재 재생", 
                   value=info['current']['title'][:50] if info['current'] else "없음", 
                   inline=False)
    embed.add_field(name="📋 대기열", value=f"{info['queue_length']}개", inline=True)
    embed.add_field(name="⏱️ 총 대기시간", 
                   value=f"{info['total_duration']//60}분 {info['total_duration']%60}초", 
                   inline=True)
    embed.add_field(name="🔊 재생 상태", 
                   value="▶️ 재생중" if info['is_playing'] else "⏸️ 정지", 
                   inline=True)
    embed.add_field(name="💾 캐시", value="🚫 비활성화", inline=True)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="queue", description="현재 대기열 확인")
async def queue_command(interaction: discord.Interaction):
    """대기열 확인"""
    if not player.queue:
        await interaction.response.send_message("📭 대기열이 비어있습니다.", ephemeral=True)
        return
    
    embed = discord.Embed(title="📋 현재 대기열", color=0x1DB954)
    
    queue_text = ""
    for i, track in enumerate(player.queue[:10]):  # 최대 10개만 표시
        if track.get("loading"):
            queue_text += f"{i+1}. 🔍 {track['title']}\n"
        else:
            duration = f"{track['duration']//60}:{track['duration']%60:02d}"
            queue_text += f"{i+1}. {track['title'][:40]} ({duration})\n"
    
    if len(player.queue) > 10:
        queue_text += f"\n... 외 {len(player.queue)-10}개"
    
    embed.description = queue_text if queue_text else "대기열이 비어있습니다."
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="skip", description="현재 곡 건너뛰기")
async def skip_command(interaction: discord.Interaction):
    """곡 건너뛰기"""
    if interaction.user.guild_permissions.manage_messages:
        if await player.skip():
            await interaction.response.send_message("⏭️ 다음 곡으로 넘어갑니다.", ephemeral=True)
        else:
            await interaction.response.send_message("⏸️ 현재 재생 중인 곡이 없습니다.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)

@bot.tree.command(name="stop", description="플레이어 중지")
async def stop_command(interaction: discord.Interaction):
    """플레이어 중지"""
    if interaction.user.guild_permissions.manage_messages:
        await player.stop()
        await interaction.response.send_message("🛑 플레이어를 중지했습니다.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)

# 캐시 관련 명령어들 (비활성화 알림)
@bot.tree.command(name="cache_info", description="캐시 정보 (비활성화됨)")
async def cache_info(interaction: discord.Interaction):
    """캐시 정보"""
    embed = discord.Embed(
        title="💾 캐시 정보", 
        description="캐시 기능이 비활성화되었습니다.\n\n"
                   "**이유:** YouTube 스트림 URL이 시간이 지나면 만료되어\n"
                   "캐시된 URL이 무효해지는 문제가 발생했습니다.\n\n"
                   "**현재 방식:** 매번 새로운 스트림 URL을 생성하여\n"
                   "안정적인 재생을 보장합니다.",
        color=0xff9500
    )
    embed.add_field(name="🔄 처리 방식", value="실시간 URL 생성", inline=True)
    embed.add_field(name="⚡ 성능", value="검색 속도 최적화", inline=True)
    embed.add_field(name="🛡️ 안정성", value="URL 만료 없음", inline=True)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# 종료 시 리소스 정리
async def cleanup():
    """정상 종료 처리"""
    print("🔄 봇 종료 준비 중...")
    await player.shutdown_handler()
    await bot.close()
    print("👋 봇 종료 완료")

def signal_handler(sig, frame):
    """시그널 핸들러"""
    print("⏹️ 종료 신호 받음")
    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop.create_task(cleanup())
    else:
        asyncio.run(cleanup())

# 시그널 등록
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

async def main():
    """메인 함수"""
    try:
        # 봇 실행 (슬래시 커맨드 동기화는 on_ready에서)
        await bot.start(config.BOT_TOKEN)
        
    except KeyboardInterrupt:
        print("👋 사용자에 의한 종료")
    except Exception as e:
        logger.error(f"❌ 봇 실행 오류: {e}")
    finally:
        await cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 봇 종료")
    except Exception as e:
        logger.error(f"❌ 메인 실행 오류: {e}")
        logger.error(f"❌ 슬래시 커맨드 동기화 실패: {e}")