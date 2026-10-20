import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

EXTENSIONS = (
    "cogs.general",
    "cogs.f1",
    "cogs.link_fix",
)


class Bot(commands.Bot):
    async def setup_hook(self):
        for extension in EXTENSIONS:
            await self.load_extension(extension)

    async def on_ready(self):
        print(f"{self.user.name} is ready for duty")

    async def on_message(self, message: discord.Message):
        await self.process_commands(message)


def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is missing. Add it to the .env file.")

    handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="w")
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    logging.getLogger("discord").setLevel(logging.DEBUG)
    logging.getLogger("discord").addHandler(handler)
    bot_log = logging.getLogger("bot")
    bot_log.setLevel(logging.INFO)
    bot_log.addHandler(handler)
    bot_log.addHandler(console)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    bot = Bot(command_prefix="!", intents=intents)
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
