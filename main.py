import logging
import os
import sys

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

EXTENSIONS = (
    "cogs.general",
    "cogs.f1",
    "cogs.link_fix",
    "cogs.music",
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

    # Logs go to stderr so Container Apps keeps them. No log file.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    bot = Bot(command_prefix="!", intents=intents)
    bot.run(token)


if __name__ == "__main__":
    main()
