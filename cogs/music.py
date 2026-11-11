import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field

import discord
from discord.ext import commands
import yt_dlp

log = logging.getLogger("bot.music")

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",
}
FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTIONS = "-vn"


@dataclass
class Track:
    title: str
    page: str


@dataclass
class GuildMusic:
    queue: deque[Track] = field(default_factory=deque)
    current: Track | None = None
    starting: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def resolve_track(query: str) -> Track:
    """Look up one video. Raises ValueError for live streams and missing results."""
    with yt_dlp.YoutubeDL(YTDL_OPTIONS) as downloader:
        info = downloader.extract_info(query, download=False)
    if info is None:
        raise ValueError("No video found.")
    if "entries" in info:
        entries = [entry for entry in info["entries"] if entry]
        if not entries:
            raise ValueError("No video found.")
        info = entries[0]
    if info.get("is_live"):
        raise ValueError("Live videos are not supported.")
    page = info.get("webpage_url") or query
    title = info.get("title") or query
    if not info.get("url"):
        raise ValueError("No audio stream.")
    return Track(title=title, page=page)


def stream_url(page: str) -> str:
    """Resolve a fresh audio URL. YouTube links expire, so this runs at play time."""
    with yt_dlp.YoutubeDL(YTDL_OPTIONS) as downloader:
        info = downloader.extract_info(page, download=False)
    if info is None or not info.get("url"):
        raise ValueError("No audio stream.")
    return info["url"]


class Music(commands.Cog):
    """Play YouTube audio in a voice channel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._states: dict[int, GuildMusic] = {}

    def _state(self, guild_id: int) -> GuildMusic:
        state = self._states.get(guild_id)
        if state is None:
            state = GuildMusic()
            self._states[guild_id] = state
        return state

    async def _connect(self, ctx: commands.Context) -> discord.VoiceClient | None:
        author_voice = ctx.author.voice
        if author_voice is None or author_voice.channel is None:
            await ctx.send("Join a voice channel first.")
            return None
        channel = author_voice.channel
        voice = ctx.voice_client
        try:
            if voice is None:
                return await channel.connect()
            if voice.channel != channel:
                await voice.move_to(channel)
            return voice
        except discord.ClientException as exc:
            log.warning("Could not join voice: %s", exc)
            await ctx.send("I couldn't join that voice channel. Check Connect and Speak, and that ffmpeg is installed.")
            return None

    async def _advance(self, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        if guild is None or guild.voice_client is None:
            return
        await self._start_next(guild, guild.voice_client)

    async def _start_next(self, guild: discord.Guild, voice: discord.VoiceClient):
        state = self._state(guild.id)
        async with state.lock:
            if voice.is_playing() or voice.is_paused() or state.starting:
                return
            if not state.queue:
                state.current = None
                return
            track = state.queue.popleft()
            state.current = track
            state.starting = True
        try:
            url = await asyncio.to_thread(stream_url, track.page)
            source = discord.FFmpegPCMAudio(url, before_options=FFMPEG_BEFORE, options=FFMPEG_OPTIONS)

            def after(error: BaseException | None):
                if error:
                    log.error("Playback error for %s: %s", track.title, error)
                asyncio.run_coroutine_threadsafe(self._advance(guild.id), self.bot.loop)

            voice.play(source, after=after)
        except discord.ClientException:
            log.exception("ffmpeg is not available")
            state.current = None
            state.starting = False
            raise
        except Exception:
            log.exception("Could not stream %s", track.title)
            state.current = None
            state.starting = False
            await self._start_next(guild, voice)
            return
        state.starting = False

    @commands.command()
    @commands.guild_only()
    async def join(self, ctx: commands.Context):
        """Join the voice channel you are in."""
        if await self._connect(ctx):
            await ctx.send(f"Joined {ctx.author.voice.channel.name}.")

    @commands.command()
    @commands.guild_only()
    async def play(self, ctx: commands.Context, *, query: str):
        """Play a YouTube link or search for a track and queue it."""
        voice = await self._connect(ctx)
        if voice is None:
            return
        try:
            async with ctx.typing():
                track = await asyncio.to_thread(resolve_track, query)
        except Exception:
            log.exception("Could not resolve %s", query)
            await ctx.send("Couldn't find audio for that. Age-restricted and live videos are not supported.")
            return

        state = self._state(ctx.guild.id)
        async with state.lock:
            state.queue.append(track)
        await ctx.send(f"Added **{track.title}** to the queue.")
        try:
            await self._start_next(ctx.guild, voice)
        except discord.ClientException:
            await ctx.send("ffmpeg is not installed, so I can't play audio.")

    @commands.command()
    @commands.guild_only()
    async def skip(self, ctx: commands.Context):
        """Skip the current track."""
        voice = ctx.voice_client
        if voice is None or not (voice.is_playing() or voice.is_paused()):
            await ctx.send("Nothing is playing.")
            return
        voice.stop()
        await ctx.send("Skipped.")

    @commands.command()
    @commands.guild_only()
    async def stop(self, ctx: commands.Context):
        """Stop playback and clear the queue."""
        state = self._states.get(ctx.guild.id)
        if state is not None:
            state.queue.clear()
            state.current = None
        voice = ctx.voice_client
        if voice is not None and (voice.is_playing() or voice.is_paused()):
            voice.stop()
        await ctx.send("Stopped.")

    @commands.command(name="queue")
    @commands.guild_only()
    async def show_queue(self, ctx: commands.Context):
        """Show the current track and what is queued."""
        state = self._states.get(ctx.guild.id)
        if state is None or (state.current is None and not state.queue):
            await ctx.send("The queue is empty.")
            return
        lines = []
        if state.current is not None:
            lines.append(f"Now playing: **{state.current.title}**")
        for index, track in enumerate(state.queue, start=1):
            lines.append(f"{index}. {track.title}")
        await ctx.send("\n".join(lines)[:2000])

    @commands.command()
    @commands.guild_only()
    async def leave(self, ctx: commands.Context):
        """Clear the queue and disconnect."""
        state = self._states.pop(ctx.guild.id, None)
        if state is not None:
            state.queue.clear()
        voice = ctx.voice_client
        if voice is None:
            await ctx.send("I'm not in a voice channel.")
            return
        if voice.is_playing() or voice.is_paused():
            voice.stop()
        await voice.disconnect()
        await ctx.send("Left the voice channel.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
