import discord
from discord.ext import commands
import config
from music.player import Player
import signal
import asyncio
import logging
import sys
from datetime import datetime

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 버전 확인
print(f"🐍 Python 버전: {sys.version}")
print(f"📦 Discord.py 버전: {discord.__version__}")

# Discord.py 버전이 2.0 이상인지 확인
if not discord.__version__.startswith('2.'):
    print("⚠️ Discord.py 2.0+ 필요! pip install -U discord.py")

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

# setup_hook 정의 (더 안정적인 동기화)
async def setup_hook():
    """봇 설정 후크 - 동기화 전용"""
    try:
        print("🔄 슬래시 커맨드 동기화 시작...")
        
        # 등록된 명령어 확인
        all_commands = bot.tree.get_commands()
        print(f"📋 등록될 명령어 수: {len(all_commands)}")
        
        for cmd in all_commands:
            print(f"  - /{cmd.name}: {cmd.description}")
        
        if len(all_commands) == 0:
            print("⚠️ 등록된 명령어가 없습니다!")
            return
        
        # 글로벌 동기화
        synced = await bot.tree.sync()
        print(f"✅ 글로벌 동기화 완료: {len(synced)}개 명령어")
        
        if len(synced) > 0:
            print("🎉 새로운 명령어들이 동기화되었습니다!")
            for cmd in synced:
                # AppCommand 객체의 올바른 속성 접근
                if hasattr(cmd, 'name'):
                    print(f"  - {cmd.name}")
                else:
                    print(f"  - {str(cmd)}")
        else:
            print("⚠️ 동기화된 명령어가 없습니다.")
            
    except Exception as e:
        print(f"❌ 동기화 실패: {e}")
        import traceback
        traceback.print_exc()

# setup_hook 등록
bot.setup_hook = setup_hook

@bot.event  
async def on_ready():
    """봇 준비 완료 - 동기화는 setup_hook에서 처리"""
    print(f"✅ Logged in as {bot.user}")
    print(f"🌐 {len(bot.guilds)}개 서버에 연결됨")
    
    # 플레이어 초기화만
    await player.initialize()
    print("🎵 플레이어 초기화 완료")

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

# === YouTube 믹스 관련 슬래시 커맨드 ===

@bot.tree.command(
    name="mix", 
    description="현재 재생 중인 곡의 YouTube 믹스에서 곡들을 추가합니다"
)
async def mix_command(interaction: discord.Interaction, count: int = 10):
    """YouTube 믹스에서 곡 추가"""
    await interaction.response.defer(ephemeral=True)
    
    if count > 30:
        await interaction.followup.send("❌ 최대 30곡까지만 추가할 수 있습니다.", ephemeral=True)
        return
    elif count < 1:
        await interaction.followup.send("❌ 최소 1곡 이상 입력해주세요.", ephemeral=True)
        return
    
    guild_player = player.get_player(interaction.guild_id)
    if not guild_player:
        await interaction.followup.send("❌ 음악 플레이어가 설정되지 않았습니다.", ephemeral=True)
        return
    
    if not guild_player.current:
        await interaction.followup.send("❌ 현재 재생 중인 곡이 없습니다.", ephemeral=True)
        return
    
    current_track = guild_player.current[0]
    current_url = current_track.get('video_url', '')
    
    if not current_url:
        await interaction.followup.send("❌ 현재 곡의 URL을 찾을 수 없습니다.", ephemeral=True)
        return
    
    video_id = guild_player.youtube_mix_queue.extract_video_id(current_url)
    if not video_id:
        await interaction.followup.send("❌ 현재 곡의 비디오 ID를 추출할 수 없습니다.", ephemeral=True)
        return
    
    result = await guild_player.youtube_mix_queue.add_mix_songs_by_command(video_id, count)
    
    if result['success']:
        embed = discord.Embed(
            title="🎲 YouTube 믹스 추가 완료",
            description=result['message'],
            color=0x1DB954
        )
        embed.add_field(
            name="📋 기준 곡",
            value=f"{current_track['title'][:50]}",
            inline=False
        )
        embed.add_field(
            name="➕ 추가된 곡 수",
            value=f"{result['added_count']}곡",
            inline=True
        )
        embed.add_field(
            name="👤 요청자",
            value="YouTube 알고리즘",
            inline=True
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.followup.send(f"❌ {result['message']}", ephemeral=True)

@bot.tree.command(
    name="mixurl", 
    description="특정 YouTube URL의 믹스에서 곡들을 추가합니다"
)
async def mixurl_command(interaction: discord.Interaction, url: str, count: int = 10):
    """특정 URL의 YouTube 믹스에서 곡 추가"""
    await interaction.response.defer(ephemeral=True)
    
    if count > 30:
        await interaction.followup.send("❌ 최대 30곡까지만 추가할 수 있습니다.", ephemeral=True)
        return
    elif count < 1:
        await interaction.followup.send("❌ 최소 1곡 이상 입력해주세요.", ephemeral=True)
        return
    
    guild_player = player.get_player(interaction.guild_id)
    if not guild_player:
        await interaction.followup.send("❌ 음악 플레이어가 설정되지 않았습니다.", ephemeral=True)
        return
    
    video_id = guild_player.youtube_mix_queue.extract_video_id(url)
    if not video_id:
        await interaction.followup.send("❌ 유효하지 않은 YouTube URL입니다.", ephemeral=True)
        return
    
    result = await guild_player.youtube_mix_queue.add_mix_songs_by_command(video_id, count)
    
    if result['success']:
        embed = discord.Embed(
            title="🎲 YouTube 믹스 추가 완료",
            description=result['message'],
            color=0x1DB954
        )
        embed.add_field(
            name="📋 기준 URL",
            value=f"[링크 보기]({url})",
            inline=False
        )
        embed.add_field(
            name="➕ 추가된 곡 수",
            value=f"{result['added_count']}곡",
            inline=True
        )
        embed.add_field(
            name="👤 요청자",
            value="YouTube 알고리즘",
            inline=True
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.followup.send(f"❌ {result['message']}", ephemeral=True)

@bot.tree.command(name="clear_mix_cache", description="믹스 캐시를 지웁니다 (관리자 전용)")
async def clear_mix_cache(interaction: discord.Interaction):
    """믹스 캐시 지우기"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    
    guild_player = player.get_player(interaction.guild_id)
    if guild_player and hasattr(guild_player, 'youtube_mix_queue'):
        cache_count = len(guild_player.youtube_mix_queue.mix_cache)
        stream_cache_count = len(guild_player.youtube_mix_queue.stream_cache)
        guild_player.youtube_mix_queue.mix_cache.clear()
        guild_player.youtube_mix_queue.stream_cache.clear()
        await interaction.response.send_message(
            f"🧹 믹스 캐시 {cache_count}개, 스트림 캐시 {stream_cache_count}개 항목이 지워졌습니다.", 
            ephemeral=True
        )
    else:
        await interaction.response.send_message("❌ 플레이어를 찾을 수 없습니다.", ephemeral=True)

# === 디버깅용 명령어 ===

@bot.tree.command(name="debug_commands", description="등록된 모든 명령어를 확인합니다 (관리자 전용)")
async def debug_commands(interaction: discord.Interaction):
    """등록된 명령어 디버깅"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    
    all_commands = bot.tree.get_commands()
    
    embed = discord.Embed(
        title="🔍 등록된 슬래시 명령어",
        description=f"총 {len(all_commands)}개의 명령어가 등록됨",
        color=0x00ff00
    )
    
    command_list = ""
    for i, cmd in enumerate(all_commands, 1):
        command_list += f"{i}. `/{cmd.name}` - {cmd.description}\n"
        if len(command_list) > 1800:  # 임베드 길이 제한
            command_list += "... (더 많은 명령어 있음)"
            break
    
    embed.add_field(name="명령어 목록", value=command_list or "명령어가 없습니다.", inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="force_sync", description="모든 슬래시 명령어를 강제로 동기화합니다 (관리자 전용)")
async def force_sync_all(interaction: discord.Interaction):
    """모든 서버에 슬래시 명령어 강제 동기화"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        # 글로벌 동기화
        global_synced = await bot.tree.sync()
        
        # 현재 서버 동기화
        guild_synced = await bot.tree.sync(guild=interaction.guild)
        
        embed = discord.Embed(
            title="🔄 강제 동기화 완료",
            color=0x00ff00
        )
        embed.add_field(name="글로벌", value=f"{len(global_synced)}개 명령어", inline=True)
        embed.add_field(name="현재 서버", value=f"{len(guild_synced)}개 명령어", inline=True)
        embed.add_field(
            name="⚠️ 주의사항", 
            value="명령어가 나타나는데 최대 1시간까지 걸릴 수 있습니다.", 
            inline=False
        )
        
        # 글로벌 동기화된 명령어 목록
        if global_synced:
            global_names = []
            for cmd in global_synced:
                if hasattr(cmd, 'name'):
                    global_names.append(cmd.name)
                else:
                    global_names.append(str(cmd))
            
            embed.add_field(
                name="글로벌 동기화된 명령어",
                value=", ".join(global_names[:10]) if global_names else "없음",
                inline=False
            )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        await interaction.followup.send(f"❌ 동기화 실패: {e}", ephemeral=True)
        import traceback
        traceback.print_exc()

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

# === 임시 텍스트 명령어들 (디버깅용) ===

@bot.command(name='명령어확인')
async def check_commands(ctx):
    """등록된 슬래시 명령어 확인"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ 관리자 권한이 필요합니다.")
        return
    
    commands = bot.tree.get_commands()
    
    embed = discord.Embed(
        title="📋 등록된 슬래시 명령어",
        description=f"총 {len(commands)}개",
        color=0x00ff00
    )
    
    command_text = ""
    for cmd in commands:
        command_text += f"• **/{cmd.name}** - {cmd.description}\n"
    
    if command_text:
        embed.add_field(name="명령어 목록", value=command_text[:1024], inline=False)
    else:
        embed.add_field(name="⚠️", value="등록된 명령어가 없습니다.", inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name='강제동기화')
async def force_sync_now(ctx):
    """강제 동기화"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ 관리자 권한이 필요합니다.")
        return
    
    try:
        await ctx.send("🔄 동기화 시작...")
        
        # 등록된 명령어 확인
        commands = bot.tree.get_commands()
        
        if len(commands) == 0:
            await ctx.send("❌ 등록된 명령어가 없습니다! 봇을 재시작해주세요.")
            return
        
        # 동기화 실행
        synced = await bot.tree.sync()
        
        embed = discord.Embed(
            title="✅ 동기화 완료",
            color=0x00ff00
        )
        embed.add_field(name="동기화된 명령어", value=f"{len(synced)}개", inline=True)
        embed.add_field(name="등록된 명령어", value=f"{len(commands)}개", inline=True)
        
        if len(synced) != len(commands):
            embed.add_field(
                name="⚠️ 주의", 
                value="동기화된 명령어 수가 다릅니다.", 
                inline=False
            )
        
        # 동기화된 명령어 목록 표시
        synced_names = []
        for cmd in synced:
            if hasattr(cmd, 'name'):
                synced_names.append(cmd.name)
            else:
                synced_names.append(str(cmd))
        
        if synced_names:
            embed.add_field(
                name="동기화된 명령어 목록",
                value=", ".join(synced_names[:10]),  # 최대 10개까지만 표시
                inline=False
            )
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ 동기화 실패: {e}")
        import traceback
        traceback.print_exc()

# 명령어 등록 상태를 실시간으로 확인하는 코드
def check_command_registration():
    """명령어 등록 상태 확인"""
    commands = bot.tree.get_commands()
    print(f"\n🔍 현재 등록된 명령어: {len(commands)}개")
    
    expected_commands = ['mix', 'mixurl', 'setup_music', 'status', 'queue', 'debug_commands', 'force_sync']
    registered_commands = [cmd.name for cmd in commands]
    
    for expected in expected_commands:
        if expected in registered_commands:
            print(f"  ✅ /{expected}")
        else:
            print(f"  ❌ /{expected} (누락됨)")
    
    print("")

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

# 명령어 등록 상태 확인 (봇 시작 전)
print("🔍 명령어 등록 상태 확인:")
check_command_registration()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 봇 종료")
    except Exception as e:
        logger.error(f"❌ 메인 실행 오류: {e}")