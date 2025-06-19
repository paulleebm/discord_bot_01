import discord
from discord.ext import commands
import config
from music.player import Player

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

bot.run(config.BOT_TOKEN)
