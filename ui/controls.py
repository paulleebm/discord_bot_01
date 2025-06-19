# ui/controls.py - 멀티 서버 지원 버전

import discord
from discord.ui import View, Button, Select
from discord import SelectOption
from datetime import timedelta

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
        
        # 대기열이 있을 때만 드롭다운 추가
        if guild_player.queue:
            self.add_item(MusicDropdown(guild_player))

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.blurple)
    async def skip_button(self, interaction: discord.Interaction, button: Button):
        # 권한 확인
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return
        
        if self.guild_player.vc and self.guild_player.vc.is_playing():
            self.guild_player.vc.stop()
            await interaction.response.send_message("⏭️ 다음 곡으로 이동합니다.", ephemeral=True)
        else:
            await interaction.response.send_message("⏸️ 현재 재생 중인 곡이 없습니다.", ephemeral=True)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.red)
    async def stop_button(self, interaction: discord.Interaction, button: Button):
        # 권한 확인
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return
        
        await self.guild_player.stop()
        await interaction.response.send_message("🛑 재생을 중지하고 연결을 종료했습니다.", ephemeral=True)