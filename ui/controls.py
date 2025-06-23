# ui/controls.py - 완전판

import discord
from discord.ui import View, Button, Select
from discord import SelectOption, ButtonStyle
from datetime import timedelta
import asyncio
import logging
import time

logger = logging.getLogger(__name__)

class MusicDropdown(Select):
    def __init__(self, guild_player):
        options = []
        placeholder_text = "대기열 관리"
        
        for i, track in enumerate(guild_player.queue[:25]):
            # 로딩 중인 트랙 처리
            if track.get("loading"):
                label = f"{i+1}. {track['title']}"
                desc = "검색 중..."
            else:
                label = f"{i+1}. {track['title']}"
                duration = track.get('duration', 0)
                desc = f"{duration//60}:{duration%60:02d} - 클릭하여 삭제"
            
            options.append(SelectOption(
                label=label[:100], 
                description=desc[:100],
                value=str(i)
            ))
        
        # 대기열이 있으면 첫 번째 곡을 placeholder로 설정
        if options:
            first_track = guild_player.queue[0]
            if first_track.get("loading"):
                placeholder_text = f"🔍 {first_track['title'][:40]}..."
            else:
                placeholder_text = f"1. {first_track['title'][:40]}"
        
        if not options:
            options = [SelectOption(label="대기열이 비어있습니다.", description="곡을 추가해주세요", value="empty")]
            placeholder_text = "대기열이 비어있습니다"
        
        super().__init__(placeholder=placeholder_text, max_values=1, min_values=1, options=options)
        self.guild_player = guild_player

    async def callback(self, interaction: discord.Interaction):
        if not self.guild_player.queue or self.values[0] == "empty":
            await interaction.response.send_message("대기열이 비어있습니다.", ephemeral=True)
            return
        
        try:
            index = int(self.values[0])
            if 0 <= index < len(self.guild_player.queue):
                # 대기열에서 곡 제거
                track_to_remove = self.guild_player.queue.pop(index)
                await self.guild_player.update_ui()
                
                # 로딩 중인 트랙인지 확인
                if track_to_remove.get("loading"):
                    await interaction.response.send_message(
                        f"검색 중인 곡을 대기열에서 제거했습니다.", 
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        f"대기열에서 곡 '{track_to_remove['title']}'을(를) 삭제하였습니다.", 
                        ephemeral=True
                    )
            else:
                await interaction.response.send_message("해당 곡을 찾을 수 없습니다.", ephemeral=True)
        except (ValueError, IndexError):
            await interaction.response.send_message("잘못된 선택입니다.", ephemeral=True)

class MusicView(View):
    def __init__(self, guild_player):
        super().__init__(timeout=None)
        self.guild_player = guild_player
        self._last_interaction = {}
        self._processing_users = set()  # 처리 중인 사용자 추적
        
        # 대기열이 있을 때만 드롭다운 추가
        if guild_player.queue:
            self.add_item(MusicDropdown(guild_player))

    async def _check_interaction_cooldown(self, interaction: discord.Interaction, cooldown_seconds: float = 3.0) -> bool:
        """상호작용 쿨다운 체크"""
        user_id = interaction.user.id
        current_time = time.time()
        
        # 이미 처리 중인 사용자 체크
        if user_id in self._processing_users:
            await interaction.response.send_message(
                "⚠️ 이미 요청을 처리 중입니다. 잠시만 기다려주세요.",
                ephemeral=True
            )
            return False
        
        # 재생 중일 때는 쿨다운 증가
        if self.guild_player.vc and self.guild_player.vc.is_playing():
            cooldown_seconds *= 1.5
        
        if user_id in self._last_interaction:
            time_diff = current_time - self._last_interaction[user_id]
            if time_diff < cooldown_seconds:
                remaining = cooldown_seconds - time_diff
                await interaction.response.send_message(
                    f"⏳ 너무 빠른 요청입니다. {remaining:.1f}초 후에 다시 시도해주세요.",
                    ephemeral=True
                )
                return False
        
        self._last_interaction[user_id] = current_time
        self._processing_users.add(user_id)  # 처리 시작
        return True

    @discord.ui.button(label="⏸️", style=ButtonStyle.secondary, row=0)
    async def pause_button(self, interaction: discord.Interaction, button: Button):
        """정지/재생 버튼"""
        try:
            if not await self._check_interaction_cooldown(interaction, 2.0):
                return
            
            if not self.guild_player.vc:
                await interaction.response.send_message("❌ 음성 채널에 연결되지 않았습니다.", ephemeral=True)
                return
            
            if self.guild_player.vc.is_playing():
                self.guild_player.vc.pause()
                button.label = "▶️ 재생"
                await interaction.response.edit_message(view=self)
            elif self.guild_player.vc.is_paused():
                self.guild_player.vc.resume()
                button.label = "⏸️ 정지"
                await interaction.response.edit_message(view=self)
            else:
                await interaction.response.send_message("⏸️ 재생 중인 음악이 없습니다.", ephemeral=True)
                
        except Exception as e:
            logger.error(f"❌ 정지 버튼 오류: {e}")
            await interaction.response.send_message("❌ 오류가 발생했습니다.", ephemeral=True)
        finally:
            if interaction.user.id in self._processing_users:
                self._processing_users.remove(interaction.user.id)

    @discord.ui.button(label="⏭️", style=ButtonStyle.secondary, row=0)
    async def skip_button(self, interaction: discord.Interaction, button: Button):
        """건너뛰기 버튼"""
        try:
            if not await self._check_interaction_cooldown(interaction, 2.0):
                return
            
            if not self.guild_player.vc or not self.guild_player.vc.is_playing():
                await interaction.response.send_message("⏸️ 재생 중인 음악이 없습니다.", ephemeral=True)
                return
            
            # 현재 재생 중인 곡 정보
            current_title = "알 수 없음"
            if self.guild_player.current:
                current_title = self.guild_player.current[0].get('title', '알 수 없음')[:30]
            
            self.guild_player.vc.stop()
            await interaction.response.send_message(f"⏭️ '{current_title}'을(를) 건너뛰었습니다.", ephemeral=True)
            
        except Exception as e:
            logger.error(f"❌ 건너뛰기 버튼 오류: {e}")
            await interaction.response.send_message("❌ 오류가 발생했습니다.", ephemeral=True)
        finally:
            if interaction.user.id in self._processing_users:
                self._processing_users.remove(interaction.user.id)


    @discord.ui.button(label="+20", style=ButtonStyle.success, row=0)
    async def mix20_button(self, interaction: discord.Interaction, button: Button):
        """믹스 20곡 추가"""
        await self._handle_mix_button(interaction, 20)
        
    @discord.ui.button(label="🛑", style=ButtonStyle.danger, row=0)
    async def stop_button(self, interaction: discord.Interaction, button: Button):
        """완전 중지 버튼"""
        try:
            if not await self._check_interaction_cooldown(interaction, 3.0):
                return
            
            if not self.guild_player.vc:
                await interaction.response.send_message("❌ 음성 채널에 연결되지 않았습니다.", ephemeral=True)
                return
            
            # 완전 중지
            self.guild_player.queue.clear()
            self.guild_player.current = []
            if self.guild_player.vc.is_playing():
                self.guild_player.vc.stop()
            
            await self.guild_player.update_ui()
            await interaction.response.send_message("🛑 플레이어를 완전히 중지했습니다.", ephemeral=True)
            
        except Exception as e:
            logger.error(f"❌ 중지 버튼 오류: {e}")
            await interaction.response.send_message("❌ 오류가 발생했습니다.", ephemeral=True)
        finally:
            if interaction.user.id in self._processing_users:
                self._processing_users.remove(interaction.user.id)

    async def _handle_mix_button(self, interaction: discord.Interaction, count: int):
        """믹스 버튼 처리 로직"""
        user_id = interaction.user.id
        try:
            if not await self._check_interaction_cooldown(interaction, 5.0):
                return
            
            await interaction.response.defer(ephemeral=True)
            
            # 현재 재생 중인 곡 확인
            if not self.guild_player.current:
                await interaction.followup.send(
                    f"❌ 현재 재생 중인 곡이 없습니다.\n곡을 먼저 재생한 후 믹스 기능을 사용하세요.", 
                    ephemeral=True
                )
                return
            
            current_track = self.guild_player.current[0]
            current_url = current_track.get('video_url', '')
            
            if not current_url:
                await interaction.followup.send("❌ 현재 곡의 URL을 찾을 수 없습니다.", ephemeral=True)
                return
            
            if not hasattr(self.guild_player, 'youtube_mix_queue'):
                await interaction.followup.send("❌ 믹스 기능이 초기화되지 않았습니다.", ephemeral=True)
                return
                
            video_id = self.guild_player.youtube_mix_queue.extract_video_id(current_url)
            if not video_id:
                await interaction.followup.send("❌ 현재 곡의 비디오 ID를 추출할 수 없습니다.", ephemeral=True)
                return
            
            # 간단한 확인 메시지만
            await interaction.followup.send(f"🎲 믹스 {count}곡 추가 시작", ephemeral=True)
            
            # 백그라운드에서 믹스 추가 처리
            asyncio.create_task(self._process_mix_addition_delayed(video_id, count, user_id))
            
        except Exception as e:
            logger.error(f"❌ 믹스 버튼 처리 오류: {e}")
            try:
                await interaction.followup.send(f"❌ 오류 발생", ephemeral=True)
            except:
                pass
        finally:
            # 처리 완료는 백그라운드에서 처리
            pass
    
    async def _process_mix_addition_delayed(self, video_id: str, count: int, user_id: int):
        """백그라운드에서 믹스 추가 처리"""
        try:
            # 재생 중이면 더 긴 지연
            if self.guild_player.vc and self.guild_player.vc.is_playing():
                await asyncio.sleep(2.0)
            else:
                await asyncio.sleep(0.5)
            
            # 믹스에서 곡 추가
            result = await self.guild_player.youtube_mix_queue.add_mix_songs_by_command(video_id, count)
            
            # 결과는 로그로만 확인
            if result['success']:
                logger.info(f"✅ 믹스 {result['added_count']}곡 즉시 추가 완료 (사용자: {user_id})")
            else:
                logger.warning(f"⚠️ 믹스 추가 실패: {result['message']} (사용자: {user_id})")
                
        except Exception as e:
            logger.error(f"❌ 백그라운드 믹스 처리 오류: {e} (사용자: {user_id})")
        finally:
            # 처리 완료
            if user_id in self._processing_users:
                self._processing_users.remove(user_id)