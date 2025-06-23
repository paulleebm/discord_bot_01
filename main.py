# main.py - 완전한 음악 봇 메인 파일

import discord
from discord.ext import commands
import config
from music.player import get_player, cleanup_player
from ui.controls import MusicView
import logging
import asyncio
import os
import signal
import sys

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# 디스코드 라이브러리 로그 레벨 조정
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('discord.http').setLevel(logging.WARNING)

# 인텐트 설정
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

class MusicBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix='!',
            intents=intents,
            help_command=None,
            case_insensitive=True
        )
        self.startup_time = None
        self.ready_guilds = set()
    
    async def setup_hook(self):
        """봇 시작 시 초기 설정"""
        logger.info("🔧 봇 초기 설정 시작...")
        
        # 필요한 디렉토리 생성
        os.makedirs('logs', exist_ok=True)
        
        # 종료 시그널 핸들러 등록
        if os.name != 'nt':  # Windows가 아닌 경우
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """종료 시그널 처리"""
        logger.info(f"📡 종료 시그널 수신: {signum}")
        asyncio.create_task(self.close())
    
    async def on_ready(self):
        """봇이 준비되었을 때"""
        import datetime
        self.startup_time = datetime.datetime.now()
        
        logger.info(f'✅ 봇 로그인 완료: {self.user.name} (ID: {self.user.id})')
        logger.info(f'🌐 연결된 서버 수: {len(self.guilds)}')
        
        # 봇 상태 설정
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="🎵 음악 | !music_setup"
            )
        )
        
        # 기존 음악 채널이 설정된 서버들 초기화
        initialized_count = 0
        for guild in self.guilds:
            if config.guild_settings.is_music_enabled(guild.id):
                try:
                    player = get_player(guild.id, self)
                    success = await player.initialize()
                    if success:
                        initialized_count += 1
                        self.ready_guilds.add(guild.id)
                        logger.info(f"🎵 음악 플레이어 초기화 완료: {guild.name}")
                    else:
                        logger.warning(f"⚠️ 음악 플레이어 초기화 실패: {guild.name}")
                except Exception as e:
                    logger.error(f"❌ {guild.name} 초기화 오류: {e}")
        
        logger.info(f'🎵 음악 기능 활성화된 서버: {initialized_count}/{len(self.guilds)}')
        logger.info(f'🚀 봇 준비 완료! 업타임: {self.startup_time.strftime("%Y-%m-%d %H:%M:%S")}')
    
    async def on_guild_join(self, guild):
        """새 서버 참가 시"""
        logger.info(f"🆕 새 서버 참가: {guild.name} (ID: {guild.id}, 멤버: {guild.member_count})")
        
        # 환영 메시지 전송 시도
        try:
            # 시스템 채널 또는 첫 번째 텍스트 채널 찾기
            channel = guild.system_channel
            if not channel:
                channel = next((ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages), None)
            
            if channel:
                embed = discord.Embed(
                    title="🎵 음악 봇에 오신 것을 환영합니다!",
                    description=(
                        "안녕하세요! 이 봇은 Discord에서 음악을 재생할 수 있는 봇입니다.\n\n"
                        "**사용 방법:**\n"
                        "1. `!music_setup` - 음악 채널 설정\n"
                        "2. 설정된 채널에서 곡 제목 입력\n"
                        "3. 버튼을 사용해 음악 제어\n\n"
                        "관리자 권한이 있는 사용자만 설정할 수 있습니다."
                    ),
                    color=0x1DB954
                )
                embed.set_footer(text="개발자: 당신의 이름 | 문의사항은 DM으로")
                await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"❌ 환영 메시지 전송 실패 ({guild.name}): {e}")
    
    async def on_guild_remove(self, guild):
        """서버 탈퇴 시"""
        logger.info(f"👋 서버 탈퇴: {guild.name} (ID: {guild.id})")
        
        # 해당 서버의 플레이어와 설정 정리
        try:
            await cleanup_player(guild.id)
            config.guild_settings.remove_guild(guild.id)
            if guild.id in self.ready_guilds:
                self.ready_guilds.remove(guild.id)
        except Exception as e:
            logger.error(f"❌ 서버 정리 오류 ({guild.name}): {e}")
    
    async def on_message(self, message):
        """메시지 처리"""
        # 봇 메시지 무시
        if message.author.bot:
            return
        
        # 기본 명령어 처리
        await self.process_commands(message)
        
        # 음악 채널에서 메시지 처리
        if (message.guild and 
            config.guild_settings.is_music_enabled(message.guild.id) and
            message.channel.id == config.guild_settings.get_music_channel(message.guild.id)):
            
            try:
                player = get_player(message.guild.id, self)
                await player.handle_message(message)
            except Exception as e:
                logger.error(f"❌ 음악 메시지 처리 오류 ({message.guild.name}): {e}")
    
    async def on_voice_state_update(self, member, before, after):
        """음성 채널 상태 변경 처리"""
        if member == self.user:
            return
        
        # 봇이 혼자 남았을 때 자동 연결 해제
        if (before.channel and 
            self.user in before.channel.members and
            config.guild_settings.is_music_enabled(member.guild.id)):
            
            # 사람 멤버 수 확인 (봇 제외)
            human_members = [m for m in before.channel.members if not m.bot]
            
            if len(human_members) == 0:
                try:
                    player = get_player(member.guild.id, self)
                    if player.vc and player.vc.channel == before.channel:
                        logger.info(f"🔌 혼자 남아서 5초 후 연결 해제 예약: {member.guild.name}")
                        
                        # 5초 후 다시 확인해서 연결 해제
                        await asyncio.sleep(5)
                        
                        if (player.vc and 
                            player.vc.is_connected() and
                            len([m for m in player.vc.channel.members if not m.bot]) == 0):
                            
                            await player.vc.disconnect()
                            player.vc = None
                            logger.info(f"🔌 음성 채널 연결 해제됨: {member.guild.name}")
                            
                            # UI 업데이트
                            await player.update_ui()
                            
                except Exception as e:
                    logger.error(f"❌ 자동 연결 해제 오류 ({member.guild.name}): {e}")
    
    async def on_command_error(self, ctx, error):
        """명령어 오류 처리"""
        if isinstance(error, commands.CommandNotFound):
            return  # 존재하지 않는 명령어는 무시
        
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ 이 명령어를 사용할 권한이 없습니다. (관리자 권한 필요)")
        
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ 필수 인수가 누락되었습니다: `{error.param.name}`")
        
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ 잘못된 인수입니다. 명령어를 확인해주세요.")
        
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"❌ 명령어 쿨다운 중입니다. {error.retry_after:.1f}초 후에 다시 시도하세요.")
        
        else:
            logger.error(f"❌ 명령어 오류 ({ctx.guild.name if ctx.guild else 'DM'}): {error}")
            await ctx.send("❌ 명령어 처리 중 오류가 발생했습니다.")
    
    async def close(self):
        """봇 종료 시 정리"""
        logger.info("🔄 봇 종료 준비 중...")
        
        # 모든 플레이어 정리
        from music.player import players
        cleanup_tasks = []
        for guild_id in list(players.keys()):
            cleanup_tasks.append(cleanup_player(guild_id))
        
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
            logger.info(f"🧹 {len(cleanup_tasks)}개 플레이어 정리 완료")
        
        await super().close()
        logger.info("👋 봇 종료 완료")


# ========== 명령어 정의 ==========

@commands.has_permissions(administrator=True)
@commands.guild_only()
@commands.command(name='music_setup', aliases=['setup', '음악설정'])
async def setup_music_channel(ctx, channel: discord.TextChannel = None):
    """
    음악 채널 설정
    
    사용법: !music_setup [#채널]
    """
    if not channel:
        channel = ctx.channel
    
    try:
        # 봇 권한 확인
        permissions = channel.permissions_for(ctx.guild.me)
        missing_perms = []
        
        if not permissions.send_messages:
            missing_perms.append("메시지 보내기")
        if not permissions.embed_links:
            missing_perms.append("링크 첨부")
        if not permissions.manage_messages:
            missing_perms.append("메시지 관리")
        if not permissions.read_message_history:
            missing_perms.append("메시지 기록 보기")
        
        if missing_perms:
            await ctx.send(f"❌ {channel.mention}에서 다음 권한이 필요합니다:\n• " + "\n• ".join(missing_perms))
            return
        
        # 음성 채널 연결 권한 확인
        voice_perms_ok = any(
            vc.permissions_for(ctx.guild.me).connect and vc.permissions_for(ctx.guild.me).speak
            for vc in ctx.guild.voice_channels
        )
        
        if not voice_perms_ok:
            await ctx.send("⚠️ 음성 채널에 연결하고 말하기 권한이 필요합니다.")
        
        # 기존 설정이 있는지 확인
        existing_channel_id = config.guild_settings.get_music_channel(ctx.guild.id)
        if existing_channel_id:
            existing_channel = ctx.guild.get_channel(existing_channel_id)
            if existing_channel:
                confirm_msg = await ctx.send(
                    f"⚠️ 이미 {existing_channel.mention}이 음악 채널로 설정되어 있습니다.\n"
                    f"{channel.mention}로 변경하시겠습니까? (y/n)"
                )
                
                def check(m):
                    return (m.author == ctx.author and 
                           m.channel == ctx.channel and 
                           m.content.lower() in ['y', 'yes', 'n', 'no', 'ㅇ', 'ㄴ'])
                
                try:
                    reply = await ctx.bot.wait_for('message', check=check, timeout=30.0)
                    if reply.content.lower() in ['n', 'no', 'ㄴ']:
                        await ctx.send("❌ 설정이 취소되었습니다.")
                        return
                except asyncio.TimeoutError:
                    await ctx.send("⏰ 시간 초과로 설정이 취소되었습니다.")
                    return
                finally:
                    try:
                        await confirm_msg.delete()
                    except:
                        pass
        
        # 설정 저장
        config.guild_settings.set_music_channel(ctx.guild.id, channel.id)
        
        # 기존 플레이어 정리
        await cleanup_player(ctx.guild.id)
        
        # 음악 플레이어 메시지 생성
        embed = discord.Embed(
            title="🎵 음악 플레이어",
            description="제목을 입력하여 음악을 재생하세요",
            color=0x00ff00
        )
        embed.add_field(
            name="📖 사용 방법",
            value=(
                "• 곡 제목이나 YouTube 링크 입력\n"
                "• 버튼을 사용해서 음악 제어\n"
                "• 음성 채널에 먼저 참여하세요"
            ),
            inline=False
        )
        embed.set_footer(text="🎶 즐거운 음악 감상하세요!")
        
        # 플레이어 초기화 및 메시지 전송
        player = get_player(ctx.guild.id, ctx.bot)
        message = await channel.send(embed=embed, view=MusicView(player))
        
        config.guild_settings.set_music_message(ctx.guild.id, message.id)
        
        # 성공 메시지
        success_embed = discord.Embed(
            title="✅ 음악 채널 설정 완료",
            description=f"음악 채널이 {channel.mention}로 설정되었습니다.",
            color=0x00ff00
        )
        success_embed.add_field(
            name="🎯 다음 단계",
            value=(
                f"1. {channel.mention}로 이동\n"
                "2. 음성 채널에 참여\n"
                "3. 곡 제목 입력해서 음악 재생"
            ),
            inline=False
        )
        
        await ctx.send(embed=success_embed)
        
        # 플레이어 초기화
        await player.initialize()
        
        logger.info(f"✅ 음악 채널 설정 완료: {ctx.guild.name} -> #{channel.name}")
        
    except Exception as e:
        logger.error(f"❌ 음악 채널 설정 오류 ({ctx.guild.name}): {e}")
        await ctx.send(f"❌ 음악 채널 설정 중 오류가 발생했습니다.\n```{str(e)[:100]}```")

@commands.has_permissions(administrator=True)
@commands.guild_only()
@commands.command(name='music_remove', aliases=['remove', '음악제거'])
async def remove_music_channel(ctx):
    """
    음악 채널 설정 제거
    
    사용법: !music_remove
    """
    try:
        if not config.guild_settings.is_music_enabled(ctx.guild.id):
            await ctx.send("❌ 이 서버에는 음악 채널이 설정되어 있지 않습니다.")
            return
        
        # 확인 메시지
        embed = discord.Embed(
            title="⚠️ 음악 채널 설정 제거",
            description="정말로 음악 채널 설정을 제거하시겠습니까?\n모든 음악 데이터가 삭제됩니다.",
            color=0xff9900
        )
        
        confirm_msg = await ctx.send(embed=embed)
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")
        
        def check(reaction, user):
            return (user == ctx.author and 
                   str(reaction.emoji) in ["✅", "❌"] and 
                   reaction.message == confirm_msg)
        
        try:
            reaction, user = await ctx.bot.wait_for('reaction_add', check=check, timeout=30.0)
            
            if str(reaction.emoji) == "❌":
                await ctx.send("❌ 제거가 취소되었습니다.")
                return
            
            # 설정 제거
            config.guild_settings.remove_guild(ctx.guild.id)
            
            # 플레이어 정리
            await cleanup_player(ctx.guild.id)
            
            success_embed = discord.Embed(
                title="✅ 음악 채널 설정 제거 완료",
                description="음악 채널 설정이 제거되었습니다.",
                color=0x00ff00
            )
            await ctx.send(embed=success_embed)
            
            logger.info(f"🗑️ 음악 채널 설정 제거 완료: {ctx.guild.name}")
            
        except asyncio.TimeoutError:
            await ctx.send("⏰ 시간 초과로 제거가 취소되었습니다.")
        finally:
            try:
                await confirm_msg.delete()
            except:
                pass
        
    except Exception as e:
        logger.error(f"❌ 음악 채널 제거 오류 ({ctx.guild.name}): {e}")
        await ctx.send("❌ 음악 채널 제거 중 오류가 발생했습니다.")

@commands.command(name='music_info', aliases=['info', '정보'])
@commands.guild_only()
async def music_info(ctx):
    """
    음악 봇 정보 표시
    
    사용법: !music_info
    """
    try:
        embed = discord.Embed(
            title="🎵 음악 봇 정보",
            color=0x1DB954
        )
        
        # 기본 정보
        embed.add_field(
            name="📊 기본 정보",
            value=(
                f"**서버 수:** {len(ctx.bot.guilds)}\n"
                f"**음악 활성 서버:** {len([g for g in ctx.bot.guilds if config.guild_settings.is_music_enabled(g.id)])}\n"
                f"**업타임:** {ctx.bot.startup_time.strftime('%Y-%m-%d %H:%M:%S') if ctx.bot.startup_time else '알 수 없음'}"
            ),
            inline=True
        )
        
        # 현재 서버 정보
        is_enabled = config.guild_settings.is_music_enabled(ctx.guild.id)
        music_channel_id = config.guild_settings.get_music_channel(ctx.guild.id)
        music_channel = ctx.guild.get_channel(music_channel_id) if music_channel_id else None
        
        server_status = "✅ 활성화됨" if is_enabled else "❌ 비활성화됨"
        channel_info = music_channel.mention if music_channel else "없음"
        
        embed.add_field(
            name="🏠 현재 서버",
            value=(
                f"**상태:** {server_status}\n"
                f"**음악 채널:** {channel_info}"
            ),
            inline=True
        )
        
        # 명령어 정보
        embed.add_field(
            name="🎯 관리자 명령어",
            value=(
                "`!music_setup` - 음악 채널 설정\n"
                "`!music_remove` - 음악 채널 제거\n"
                "`!music_info` - 봇 정보 보기"
            ),
            inline=False
        )
        
        # 현재 재생 정보 (활성화된 경우)
        if is_enabled and music_channel:
            from music.player import players
            if ctx.guild.id in players:
                player = players[ctx.guild.id]
                queue_info = player.get_queue_info()
                
                current_info = "없음"
                if queue_info['current']:
                    current_track = queue_info['current']
                    current_info = f"{current_track['title'][:30]}..."
                
                embed.add_field(
                    name="🎶 현재 상태",
                    value=(
                        f"**재생 중:** {current_info}\n"
                        f"**대기열:** {queue_info['queue_length']}곡\n"
                        f"**상태:** {'재생중' if queue_info['is_playing'] else '정지'}"
                    ),
                    inline=False
                )
        
        embed.set_footer(text="🎵 즐거운 음악 감상하세요!")
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"❌ 정보 명령어 오류 ({ctx.guild.name}): {e}")
        await ctx.send("❌ 정보를 가져오는 중 오류가 발생했습니다.")

@commands.is_owner()
@commands.command(name='reload', hidden=True)
async def reload_bot(ctx):
    """봇 모듈 리로드 (봇 소유자만)"""
    try:
        # 플레이어들 정리
        from music.player import players
        for guild_id in list(players.keys()):
            await cleanup_player(guild_id)
        
        await ctx.send("🔄 모듈 리로드 완료")
        logger.info(f"🔄 봇 리로드: {ctx.author}")
        
    except Exception as e:
        await ctx.send(f"❌ 리로드 실패: {e}")
        logger.error(f"❌ 리로드 오류: {e}")

# ========== 봇 실행 ==========

def run_bot():
    """봇 실행 함수"""
    
    # 설정 확인
    if not config.BOT_TOKEN or config.BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ config.py에서 BOT_TOKEN을 설정해주세요!")
        return False
    
    if not config.YOUTUBE_API_KEY or config.YOUTUBE_API_KEY == "YOUR_YOUTUBE_API_KEY_HERE":
        logger.error("❌ config.py에서 YOUTUBE_API_KEY를 설정해주세요!")
        return False
    
    # 쿠키 파일 확인
    if not os.path.exists(config.COOKIES_FILE):
        logger.warning(f"⚠️ 쿠키 파일을 찾을 수 없습니다: {config.COOKIES_FILE}")
        logger.warning("YouTube 접근에 제한이 있을 수 있습니다.")
    
    # 봇 인스턴스 생성
    bot = MusicBot()
    
    # 명령어 추가
    bot.add_command(setup_music_channel)
    bot.add_command(remove_music_channel)
    bot.add_command(music_info)
    bot.add_command(reload_bot)
    
    try:
        logger.info("🚀 음악 봇 시작 중...")
        bot.run(config.BOT_TOKEN)
        
    except discord.LoginFailure:
        logger.error("❌ 봇 토큰이 잘못되었습니다! config.py를 확인해주세요.")
        return False
        
    except discord.PrivilegedIntentsRequired:
        logger.error("❌ 권한 있는 인텐트가 필요합니다! Discord Developer Portal에서 설정해주세요.")
        return False
        
    except Exception as e:
        logger.error(f"❌ 봇 실행 오류: {e}")
        return False
    
    finally:
        logger.info("👋 봇 종료됨")
    
    return True

if __name__ == "__main__":
    try:
        success = run_bot()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("⌨️ 사용자에 의해 중단됨")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ 예상치 못한 오류: {e}")
        sys.exit(1)