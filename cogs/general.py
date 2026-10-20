import discord
from discord.ext import commands


class General(commands.Cog):
    """Welcome messages, small keyword reactions, and basic commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await member.send(f"Welcome to the server {member.name}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        content = message.content.lower()

        if "max" in content:
            await message.channel.send("du du du du")

        if "max isn't going to win" in content:
            await message.delete()
            await message.channel.send(f"{message.author.mention} - dont say that")

        if "george russel" in content:
            await message.channel.send("<:SPOTTED:844982474865115186>")

    @commands.command()
    async def hello(self, ctx: commands.Context):
        await ctx.send(f"Hello {ctx.author.mention}!")


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
