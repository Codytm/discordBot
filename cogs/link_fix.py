import logging
import re

import discord
from discord.ext import commands

log = logging.getLogger("bot.link_fix")

WEBHOOK_NAME = "EmbedFix"

# twitter.com / x.com status links, with or without a scheme.
# Angle-bracketed links, spoilers, and code spans are left alone.
STATUS_LINK = re.compile(
    r"(?<![A-Za-z0-9@</])"
    r"(?:https?://)?"
    r"(?:(?:www|mobile|m)\.)?"
    r"(?P<host>twitter\.com|x\.com)"
    r"/(?:(?P<user>[A-Za-z0-9_]{1,15})|i(?:/web)?)/status/(?P<id>\d+)"
    r"(?:/(?:photo|video)/\d+)?"
    r"(?:\?[^\s<]*)?",
    re.IGNORECASE,
)


def _in_code(content: str, index: int) -> bool:
    return content.count("`", 0, index) % 2 == 1


def _is_spoiler(content: str, start: int, end: int) -> bool:
    return content[max(0, start - 2) : start] == "||" and content[end : end + 2] == "||"


def _skip(content: str, match: re.Match) -> bool:
    return _in_code(content, match.start()) or _is_spoiler(content, match.start(), match.end())


def fixed_url(match: re.Match) -> str:
    user = match.group("user") or "i"
    status_id = match.group("id")
    host = match.group("host").lower()
    # FxEmbed serves a Discord-compatible video/image embed from these domains.
    domain = "fixupx.com" if host == "x.com" else "fxtwitter.com"
    return f"https://{domain}/{user}/status/{status_id}"


def rewrite_status_links(content: str) -> str:
    def replace(match: re.Match) -> str:
        if _skip(content, match):
            return match.group(0)
        return fixed_url(match)

    return STATUS_LINK.sub(replace, content)


class LinkFix(commands.Cog):
    """Repost X/Twitter status links so Discord shows the image or video.

    Discord's native x.com embeds drop media. FxEmbed (fixupx.com / fxtwitter.com)
    publishes Open Graph tags Discord can render, including video.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.webhooks: dict[int, discord.Webhook] = {}

    def _webhook_parent(self, channel: discord.abc.Messageable):
        if isinstance(channel, discord.Thread):
            return channel.parent
        if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
            return channel
        return None

    async def _webhook_for(self, channel: discord.abc.Messageable) -> discord.Webhook | None:
        parent = self._webhook_parent(channel)
        if parent is None or not isinstance(parent, (discord.TextChannel, discord.ForumChannel)):
            return None

        me = parent.guild.me
        if me is None or not parent.permissions_for(me).manage_webhooks:
            return None

        cached = self.webhooks.get(parent.id)
        if cached is not None:
            return cached

        hooks = await parent.webhooks()
        for hook in hooks:
            if hook.name == WEBHOOK_NAME and hook.token:
                self.webhooks[parent.id] = hook
                return hook

        hook = await parent.create_webhook(name=WEBHOOK_NAME, reason="Repost fixed X embeds")
        self.webhooks[parent.id] = hook
        return hook

    async def _repost(self, message: discord.Message, content: str):
        """Send the fixed link, then remove the original so only one embed remains."""
        if message.guild is not None and not message.attachments and not message.stickers:
            try:
                webhook = await self._webhook_for(message.channel)
            except discord.HTTPException as exc:
                log.warning("Could not get embed webhook: %s", exc)
                webhook = None

            if webhook is not None:
                kwargs = {
                    "content": content,
                    "username": (message.author.display_name or message.author.name)[:80],
                    "avatar_url": message.author.display_avatar.url,
                    "allowed_mentions": discord.AllowedMentions.none(),
                    "wait": True,
                }
                if isinstance(message.channel, discord.Thread):
                    kwargs["thread"] = message.channel
                try:
                    await webhook.send(**kwargs)
                    await self._remove_original(message)
                    return
                except discord.HTTPException as exc:
                    log.warning("Webhook repost failed, posting as the bot instead: %s", exc)
                    self.webhooks.pop(getattr(self._webhook_parent(message.channel), "id", 0), None)

        # channel.send only needs Send Messages. A reply also needs Read Message History,
        # which this bot does not have, so Discord was rejecting the old fallback.
        author = message.author.display_name or message.author.name
        posted = f"{author}: {content}"
        if len(posted) > 2000:
            posted = content[:2000]
        await message.channel.send(posted, allowed_mentions=discord.AllowedMentions.none())
        await self._remove_original(message)

    async def _remove_original(self, message: discord.Message):
        me = message.guild.me if message.guild else None
        if me is None or not message.channel.permissions_for(me).manage_messages:
            return
        try:
            await message.delete()
        except discord.HTTPException as exc:
            log.warning("Could not delete original X link: %s", exc)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.webhook_id or not message.content:
            return

        try:
            rewritten = rewrite_status_links(message.content)
            if rewritten == message.content:
                return
            if len(rewritten) > 1900:
                rewritten = rewritten[:1900]
            log.info("Reposting X link from %s in #%s", message.author, getattr(message.channel, "name", message.channel.id))
            await self._repost(message, rewritten)
        except Exception:
            log.exception("Failed to repost an X link")


async def setup(bot: commands.Bot):
    await bot.add_cog(LinkFix(bot))
