import discord
from discord.ext import commands
import config
from music.player import Player
import signal
import asyncio
import logging

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
    print(f"✅ Logged in as {bot.user}")
    print(f"📁 캐시 파일: music_cache.json")

@bot.event
async def on_message(message):
    await player.handle_message(message)

# 캐시 관련 슬래시 커맨드 추가
@bot.tree.command(name="cache_stats", description="캐시 통계 확인")
async def cache_stats(interaction: discord.Interaction):
    """캐시 통계 확인"""
    if interaction.user.guild_permissions.manage_guild:
        stats = await player.get_cache_stats()
        embed = discord.Embed(title="📊 캐시 통계 (영구 보관)", color=0x00ff00)
        embed.add_field(name="🗂️ 저장된 곡", value=f"{stats['total_items']}개", inline=True)
        embed.add_field(name="📁 파일 크기", value=f"{stats['file_size_kb']}KB", inline=True)
        embed.add_field(name="🎵 총 재생 횟수", value=f"{stats['total_plays']}회", inline=True)
        
        if stats['oldest_cache']:
            try:
                oldest_date = datetime.fromisoformat(stats['oldest_cache']).strftime("%Y-%m-%d")
                embed.add_field(name="📅 가장 오래된 캐시", value=oldest_date, inline=True)
            except:
                pass
        
        embed.add_field(name="💾 파일 존재", value="✅" if stats['file_exists'] else "❌", inline=True)
        embed.add_field(name="♾️ 보관 정책", value="영구 보관", inline=True)
        
        if stats['total_items'] > 0:
            avg_plays = round(stats['total_plays'] / stats['total_items'], 1)
            embed.add_field(name="📈 평균 재생", value=f"{avg_plays}회/곡", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)

@bot.tree.command(name="clear_cache", description="캐시 파일 완전 삭제 (주의!)")
async def clear_cache_command(interaction: discord.Interaction):
    """캐시 삭제 - 영구 보관이므로 신중하게"""
    if interaction.user.guild_permissions.manage_guild:
        stats = await player.get_cache_stats()
        
        # 확인 메시지
        embed = discord.Embed(
            title="⚠️ 캐시 삭제 확인", 
            description=f"정말로 **{stats['total_items']}개의 캐시**를 모두 삭제하시겠습니까?\n"
                       f"총 **{stats['total_plays']}회**의 재생 기록이 사라집니다.\n\n"
                       f"**이 작업은 되돌릴 수 없습니다!**",
            color=0xff6b6b
        )
        
        await interaction.response.send_message(
            embed=embed, 
            ephemeral=True
        )
        
        # 실제 삭제는 별도 확인 없이는 하지 않음
        # 필요시 /clear_cache_confirm 명령어 추가 가능
    else:
        await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)

@bot.tree.command(name="clear_cache_confirm", description="캐시 파일 강제 삭제 (관리자만)")
async def clear_cache_confirm(interaction: discord.Interaction):
    """실제 캐시 삭제"""
    if interaction.user.guild_permissions.administrator:
        player.clear_cache()
        await interaction.response.send_message("🧹 모든 캐시가 삭제되었습니다.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)

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
        # 슬래시 커맨드 동기화
        await bot.tree.sync()
        logger.info("🔄 슬래시 커맨드 동기화 완료")
        
        # 봇 실행
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