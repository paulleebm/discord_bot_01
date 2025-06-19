import discord
from discord.ui import View, Button, Select
from discord import SelectOption
from datetime import timedelta

class MusicDropdown(Select):
    def __init__(self, player):
        options = []
        for i, track in enumerate(player.queue[:25]):
            label = f"{i+1}. {track['title']}"
            desc = str(timedelta(seconds=track['duration'])) + " - 클릭하여 삭제"
            options.append(SelectOption(label=label, description=desc))
        if not options:
            options = [SelectOption(label="대기열이 비어있습니다.")]
        super().__init__(placeholder=options[0].value, max_values=1, min_values=1, options=options)
        self.player = player

    async def callback(self, interaction: discord.Interaction):
        index = int(self.values[0].split(".")[0]) - 1
        if 0 <= index < len(self.player.queue):
            # Actually remove the track from the queue
            track_to_remove = self.player.queue.pop(index)
            await self.player.update_ui()
            await interaction.response.send_message(f"대기열에서 곡 '{track_to_remove['title']}'을(를) 삭제하였습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("대기열이 비어있습니다.", ephemeral=True)

class MusicView(View):
    def __init__(self, player):
        super().__init__(timeout=None)
        self.player = player
        self.add_item(MusicDropdown(player))

    @discord.ui.button(label="▶|", style=discord.ButtonStyle.blurple)
    async def skip_button(self, interaction: discord.Interaction, button: Button):
        if self.player.vc and self.player.vc.is_playing():
            self.player.vc.stop()
            await interaction.response.send_message("⏭ 다음 곡으로 이동합니다.", ephemeral=True)
        else:
            await interaction.response.send_message("⏸ 현재 재생 중인 곡이 없습니다.", ephemeral=True)

    @discord.ui.button(label="■", style=discord.ButtonStyle.red)
    async def stop_button(self, interaction: discord.Interaction, button: Button):
        await self.player.stop()
        await interaction.response.send_message("🛑 재생을 중지하고 연결을 종료했습니다.", ephemeral=True)
