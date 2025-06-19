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

# 인텐트 설정 (중요!)
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

# 봇 생성 (application_id 추가로 슬래시 명령어 확실히 등록)
bot = commands.Bot(
    command_prefix="/", 
    intents=intents,
    # application_id=YOUR_APPLICATION_ID  # 필요시 추가
)
player = Player(bot)

@bot.event
async def on_ready():
    """봇 준비 완료"""
    print(f"✅ Logged in as {bot.user}")
    print(f"🌐 {len(bot.guilds)}개 서버에 연결됨")
    
    # 플레이어 초기화
    await player.initialize()
    
    # 슬래시 커맨드 강제 동기화
    try:
        print("🔄 슬래시 커맨드 동기화 시작...")
        
        # 글로벌 동기화 (모든 서버)
        synced = await bot.tree.sync()
        print(f"✅ 글로벌 슬래시 커맨드 동기화 완료: {len(synced)}개")
        
        # 각 서버별로도 동기화 (확실하게)
        for guild in bot.guilds:
            try:
                guild_synced = await bot.tree.sync(guild=guild)
                print(f"✅ 서버 {guild.name}: {len(guild_synced)}개 명령어 동기화")
            except discord.HTTPException as e:
                print(f"⚠️ 서버 {guild.name} 동기화 실패: {e}")
        
        print("🎉 모든 슬래시 커맨드 동기화 완료!")
        
    except Exception as e:
        logger.error(f"❌ 슬래시 커맨드 동기화 실패: {e}")

@bot.event
async def on_guild_join(guild):
    """새 서버 참가 시 즉시 동기화"""
    logger.info(f"🆕 새 서버 참가: {guild.name} (ID: {guild.id})")
    try:
        synced = await bot.tree.sync(guild=guild)
        logger.info(f"✅ 새 서버 슬래시 명령어 동기화: {len(synced)}개")
    except Exception as e:
        logger.error(f"❌ 새 서버 동기화 실패: {e}")

@bot.event
async def on_guild_remove(guild):
    """서버 나가기 시"""
    logger.info(f"👋 서버 나감: {guild.name} (ID: {guild.id})")
    config.guild_settings.remove_guild(guild.id)

@bot.event
async def on_message(message):
    """메시지 처리"""
    await player.handle_message(message)
    # 일반 명령어도 처리하려면 추가
    await bot.process_commands(message)

# === 설정 관련 슬래시 커맨드 ===

@bot.tree.command(name="setup_music", description="음악 플레이어를 현재 채널에 설정합니다")
async def setup_music(interaction: discord.Interaction):
    """음악 플레이어 설정"""
    await interaction.response.defer(ephemeral=True)
    
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.followup.send("❌ 채널 관리 권한이 필요합니다.", ephemeral=True)
        return
    
    success, message = await player.setup_music_channel(
        interaction.guild_id, 
        interaction.channel_id
    )
    
    if success:
        await interaction.followup.send(f"✅ {message}", ephemeral=True)
    else:
        await interaction.followup.send(f"❌ {message}", ephemeral=True)

@bot.tree.command(name="music_info", description="현재 서버의 음악 설정 정보를 확인합니다")
async def music_info(interaction: discord.Interaction):
    """음악 설정 정보"""
    guild_id = interaction.guild_id
    channel_id = config.guild_settings.get_music_channel(guild_id)
    message_id = config.guild_settings.get_music_message(guild_id)
    
    embed = discord.Embed(title="🎵 음악 설정 정보", color=0x00ff00)
    
    if channel_id and message_id:
        channel = bot.get_channel(channel_id)
        embed.add_field(
            name="✅ 설정 상태", 
            value="활성화됨", 
            inline=False
        )
        embed.add_field(
            name="📺 음악 채널", 
            value=channel.mention if channel else f"채널 ID: {channel_id} (삭제됨)", 
            inline=True
        )
        embed.add_field(
            name="💬 메시지 ID", 
            value=message_id, 
            inline=True
        )
        
        # 플레이어 상태
        guild_player = player.get_player(guild_id)
        if guild_player:
            embed.add_field(
                name="🎮 플레이어 상태", 
                value="✅ 활성화", 
                inline=True
            )
        else:
            embed.add_field(
                name="🎮 플레이어 상태", 
                value="❌ 비활성화", 
                inline=True
            )
    else:
        embed.add_field(
            name="❌ 설정 상태", 
            value="비활성화됨", 
            inline=False
        )
        embed.add_field(
            name="ℹ️ 설정 방법", 
            value="음악을 사용할 채널에서 `/setup_music` 명령어를 사용하세요.", 
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="status", description="플레이어 상태를 확인합니다")
async def status(interaction: discord.Interaction):
    """플레이어 상태 확인"""
    guild_player = player.get_player(interaction.guild_id)
    
    if not guild_player:
        await interaction.response.send_message(
            "❌ 이 서버에 음악 플레이어가 설정되지 않았습니다.\n`/setup_music` 명령어를 사용하여 설정하세요.", 
            ephemeral=True
        )
        return
    
    try:
        info = guild_player.get_queue_info() if hasattr(guild_player, 'get_queue_info') else {}
        
        embed = discord.Embed(title="🎵 플레이어 상태", color=0x00ff00)
        
        current = info.get('current')
        if current:
            embed.add_field(
                name="🎵 현재 재생", 
                value=current.get('title', '알 수 없음')[:50], 
                inline=False
            )
        else:
            embed.add_field(name="🎵 현재 재생", value="없음", inline=False)
        
        embed.add_field(name="📋 대기열", value=f"{info.get('queue_length', 0)}개", inline=True)
        embed.add_field(
            name="⏱️ 총 대기시간", 
            value=f"{info.get('total_duration', 0)//60}분 {info.get('total_duration', 0)%60}초", 
            inline=True
        )
        embed.add_field(
            name="🔊 재생 상태", 
            value="▶️ 재생중" if info.get('is_playing') else "⏸️ 정지", 
            inline=True
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
    except Exception as e:
        await interaction.response.send_message(f"❌ 상태 확인 중 오류: {e}", ephemeral=True)

@bot.tree.command(name="queue", description="현재 대기열을 확인합니다")
async def queue_command(interaction: discord.Interaction):
    """대기열 확인"""
    guild_player = player.get_player(interaction.guild_id)
    
    if not guild_player:
        await interaction.response.send_message("❌ 플레이어를 찾을 수 없습니다.", ephemeral=True)
        return
    
    if not hasattr(guild_player, 'queue') or not guild_player.queue:
        await interaction.response.send_message("📭 대기열이 비어있습니다.", ephemeral=True)
        return
    
    embed = discord.Embed(title="📋 현재 대기열", color=0x1DB954)
    
    queue_text = ""
    for i, track in enumerate(guild_player.queue[:10]):
        if track.get("loading"):
            queue_text += f"{i+1}. 🔍 {track['title']}\n"
        else:
            duration = track.get('duration', 0)
            duration_str = f"{duration//60}:{duration%60:02d}"
            queue_text += f"{i+1}. {track['title'][:40]} ({duration_str})\n"
    
    if len(guild_player.queue) > 10:
        queue_text += f"\n... 외 {len(guild_player.queue)-10}개"
    
    embed.description = queue_text
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="skip", description="현재 곡을 건너뜁니다")
async def skip_command(interaction: discord.Interaction):
    """곡 건너뛰기"""
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("❌ 메시지 관리 권한이 필요합니다.", ephemeral=True)
        return
    
    guild_player = player.get_player(interaction.guild_id)
    if not guild_player:
        await interaction.response.send_message("❌ 플레이어를 찾을 수 없습니다.", ephemeral=True)
        return
    
    if hasattr(guild_player, 'skip'):
        if hasattr(guild_player, 'vc') and guild_player.vc and guild_player.vc.is_playing():
            guild_player.vc.stop()
            await interaction.response.send_message("⏭️ 다음 곡으로 넘어갑니다.", ephemeral=True)
        else:
            await interaction.response.send_message("⏸️ 현재 재생 중인 곡이 없습니다.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ 건너뛰기 기능을 사용할 수 없습니다.", ephemeral=True)

@bot.tree.command(name="stop", description="플레이어를 중지합니다")
async def stop_command(interaction: discord.Interaction):
    """플레이어 중지"""
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("❌ 메시지 관리 권한이 필요합니다.", ephemeral=True)
        return
    
    guild_player = player.get_player(interaction.guild_id)
    if not guild_player:
        await interaction.response.send_message("❌ 플레이어를 찾을 수 없습니다.", ephemeral=True)
        return
    
    if hasattr(guild_player, 'stop'):
        await guild_player.stop()
        await interaction.response.send_message("🛑 플레이어를 중지했습니다.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ 중지 기능을 사용할 수 없습니다.", ephemeral=True)

@bot.tree.command(name="reset_music", description="음악 설정을 초기화합니다 (관리자 전용)")
async def reset_music(interaction: discord.Interaction):
    """음악 설정 초기화"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    guild_id = interaction.guild_id
    
    # 플레이어 정리
    guild_player = player.get_player(guild_id)
    if guild_player:
        await guild_player.cleanup()
        if guild_id in player.guild_players:
            del player.guild_players[guild_id]
    
    # 설정 제거
    config.guild_settings.remove_guild(guild_id)
    
    await interaction.followup.send("✅ 음악 설정이 초기화되었습니다.", ephemeral=True)

# === 디버깅용 명령어 ===

@bot.tree.command(name="sync_commands", description="슬래시 명령어를 강제로 동기화합니다 (관리자 전용)")
async def sync_commands(interaction: discord.Interaction):
    """슬래시 명령어 강제 동기화"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        # 현재 서버에만 동기화
        synced = await bot.tree.sync(guild=interaction.guild)
        await interaction.followup.send(f"✅ 이 서버에 {len(synced)}개 명령어 동기화 완료!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 동기화 실패: {e}", ephemeral=True)

@bot.tree.command(name="ping", description="봇의 응답속도를 확인합니다")
async def ping(interaction: discord.Interaction):
    """핑 테스트"""
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! {latency}ms", ephemeral=True)

# 종료 처리
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
        # 봇 실행
        async with bot:
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