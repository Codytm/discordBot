import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field

import discord
from discord.ext import commands
import yt_dlp

log = logging.getLogger("bot.music")

# The default web client is what Azure's address gets challenged on.
# These clients are tried, in order, after that challenge.
YOUTUBE_CLIENTS = ("tv", "web_safari", "android_vr", "mweb")
FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -protocol_whitelist file,http,https,tcp,tls,crypto"
FFMPEG_OPTIONS = "-vn"


@dataclass
class Track:
    title: str
    page: str
    player_client: str | None = None


@dataclass
class GuildMusic:
    queue: deque[Track] = field(default_factory=deque)
    current: Track | None = None
    starting: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _options(player_client: str | None) -> dict:
    options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch1",
        "source_address": "0.0.0.0",
    }
    if player_client is not None:
        options["extractor_args"] = {"youtube": {"player_client": [player_client]}}
    return options


def _retryable(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        phrase in message
        for phrase in (
            "not a bot",
            "requested format is not available",
            "no video formats",
            "only images are available",
            "sign in to confirm your age",
        )
    )


def _video(info: dict) -> dict:
    if "entries" in info:
        entries = [entry for entry in info["entries"] if entry]
        if not entries:
            raise ValueError("No video found.")
        info = entries[0]
    if info.get("is_live"):
        raise ValueError("Live videos are not supported.")
    return info


def _extract(query: str, player_client: str | None) -> dict:
    with yt_dlp.YoutubeDL(_options(player_client)) as downloader:
        info = downloader.extract_info(query, download=False)
    if info is None:
        raise ValueError("No video found.")
    return _video(info)


def resolve_track(query: str) -> Track:
    """Look up one video. Raises ValueError for live streams and missing results."""
    last_error: Exception | None = None
    for client in (None, *YOUTUBE_CLIENTS):
        try:
            info = _extract(query, client)
        except ValueError:
            raise
        except Exception as exc:
            last_error = exc
            if _retryable(exc):
                log.warning("YouTube client %s could not resolve the track", client or "default")
                continue
            raise
        if not info.get("url"):
            last_error = ValueError("No audio stream.")
            continue
        return Track(
            title=info.get("title") or query,
            page=info.get("webpage_url") or query,
            player_client=client,
        )
    raise last_error or ValueError("No video found.")


def stream_url(page: str, player_client: str | None) -> str:
    """Resolve a fresh audio URL. YouTube links expire, so this runs at play time."""
    clients = [player_client]
    clients.extend(client for client in (None, *YOUTUBE_CLIENTS) if client != player_client)
    last_error: Exception | None = None
    for client in clients:
        try:
            info = _extract(page, client)
        except ValueError:
            raise
        except Exception as exc:
            last_error = exc
            if _retryable(exc):
                log.warning("YouTube client %s could not refresh the stream", client or "default")
                continue
            raise
        if info.get("url"):
            return info["url"]
        last_error = ValueError("No audio stream.")
    raise last_error or ValueError("No audio stream.")


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
        except Exception as exc:
            log.exception("Could not join voice")
            await ctx.send(f"I couldn't join that voice channel: {exc}")
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
            url = await asyncio.to_thread(stream_url, track.page, track.player_client)
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
        except Exception as exc:
            log.exception("Could not resolve %s", query)
            if "not a bot" in str(exc).lower():
                await ctx.send("YouTube blocked that lookup from the server. Try another link in a minute.")
            else:
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
