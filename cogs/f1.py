import asyncio
import datetime
import json
import logging
import os
import time
from pathlib import Path

import discord
from discord.ext import commands, tasks
import fastf1
from fastf1.ergast import Ergast
from fastf1.req import Cache
import pandas as pd

log = logging.getLogger("bot.f1")

F1_RED = 0xE10600
CACHE_SECONDS = 10 * 60
STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "f1_posted.json"
# Results usually are not published until the race has been running a while.
WINDOW_OPENS = pd.Timedelta(minutes=45)
WINDOW_CLOSES = pd.Timedelta(hours=8)


def _points(value) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _team_names(value) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(name) for name in value)
    if pd.isna(value):
        return ""
    return str(value)


def _code(row) -> str:
    code = row.get("driverCode")
    if isinstance(code, str) and code and not pd.isna(code):
        return code
    family = row.get("familyName") or row.get("driverId") or "?"
    if family is None or pd.isna(family):
        return "?"
    return str(family)[:3].upper()


def _unix(value) -> int | None:
    if value is None or pd.isna(value):
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return int(timestamp.timestamp())


def _race_time(value, *, winner: bool) -> str:
    if value is None or pd.isna(value):
        return ""
    seconds = value.total_seconds()
    if winner:
        hours, remainder = divmod(int(seconds), 3600)
        minutes, secs = divmod(remainder, 60)
        millis = int(round((seconds - int(seconds)) * 1000))
        return f"{hours}:{minutes:02d}:{secs:02d}.{millis:03d}"
    if seconds >= 60:
        minutes, secs = divmod(seconds, 60)
        return f"+{int(minutes)}:{secs:06.3f}"
    return f"+{seconds:.3f}s"


def _result_text(row, *, winner: bool) -> str:
    status = str(row.get("status") or "")
    if status == "Finished":
        return _race_time(row.get("totalRaceTime"), winner=winner) or status
    return status or "—"


def driver_standings_embed(response) -> discord.Embed:
    table = response.content[-1]
    season = int(response.description.iloc[-1]["season"])
    race_round = int(response.description.iloc[-1]["round"])
    lines = [
        f"{int(row['position']):>2}  {_code(row):<3}  {_points(row['points']):>5}  {int(row['wins']):>2}  {_team_names(row['constructorNames'])}"
        for row in table.to_dict("records")
    ]
    embed = discord.Embed(
        title=f"{season} Driver Standings",
        description="Position, driver, points, wins, team\n```\n" + "\n".join(lines) + "\n```",
        color=F1_RED,
    )
    embed.set_footer(text=f"After round {race_round}")
    return embed


def constructor_standings_embed(response) -> discord.Embed:
    table = response.content[-1]
    season = int(response.description.iloc[-1]["season"])
    race_round = int(response.description.iloc[-1]["round"])
    lines = [
        f"{int(row['position']):>2}  {_points(row['points']):>5}  {int(row['wins']):>2}  {row['constructorName']}"
        for row in table.to_dict("records")
    ]
    embed = discord.Embed(
        title=f"{season} Constructor Standings",
        description="Position, points, wins, team\n```\n" + "\n".join(lines) + "\n```",
        color=F1_RED,
    )
    embed.set_footer(text=f"After round {race_round}")
    return embed


def _schedule_lines(schedule: pd.DataFrame) -> list[str]:
    lines = []
    for event in schedule.itertuples(index=False):
        unix = _unix(event.Session5DateUtc)
        when = f"<t:{unix}:D>" if unix is not None else "TBD"
        lines.append(f"**R{int(event.RoundNumber):02d}** {event.EventName} — {event.Location} — {when}")
    return lines


def schedule_embed(schedule: pd.DataFrame, selection: str) -> discord.Embed:
    season = int(pd.Timestamp(schedule.iloc[0]["EventDate"]).year)
    choice = selection.lower()

    if choice in {"full", "all", "season"}:
        lines = _schedule_lines(schedule)
        return discord.Embed(
            title=f"{season} Race Calendar",
            description="\n".join(lines),
            color=F1_RED,
        )

    if choice.isdigit():
        race_round = int(choice)
        matched = schedule[schedule["RoundNumber"] == race_round]
        if matched.empty:
            last = int(schedule["RoundNumber"].max())
            raise ValueError(f"Round {race_round} is not on the {season} calendar (1–{last}).")
        event = matched.iloc[0]
        lines = []
        for index in range(1, 6):
            name = event[f"Session{index}"]
            unix = _unix(event[f"Session{index}DateUtc"])
            if pd.isna(name) or unix is None:
                continue
            lines.append(f"**{name}** — <t:{unix}:F>")
        embed = discord.Embed(
            title=f"Round {race_round}: {event['EventName']}",
            description=f"{event['Location']}, {event['Country']}\n\n" + "\n".join(lines),
            color=F1_RED,
        )
        return embed

    now = pd.Timestamp.now(tz="UTC")
    race_times = schedule["Session5DateUtc"]
    if race_times.dt.tz is None:
        race_times = race_times.dt.tz_localize("UTC")
    # Keep a race on the list until a few hours after the start.
    remaining = schedule[race_times >= now - pd.Timedelta(hours=3)]
    if remaining.empty:
        raise ValueError(f"The {season} season has no races left. Use `!schedule full` for the calendar.")
    return discord.Embed(
        title=f"{season} Remaining Races",
        description="\n".join(_schedule_lines(remaining)),
        color=F1_RED,
    )


def _race_start(value) -> pd.Timestamp | None:
    if value is None or pd.isna(value):
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp


def poll_plan(race_starts: list[tuple[pd.Timestamp, int]], now: pd.Timestamp) -> tuple[str, int | None, float]:
    """Decide whether to look up a round now or wait.

    Returns ``("poll", round, seconds)`` or ``("wait", None, seconds)``.
    ``seconds`` is how long to sleep after this decision.
    """
    upcoming = None
    for start, race_round in race_starts:
        opens = start + WINDOW_OPENS
        closes = start + WINDOW_CLOSES
        if opens <= now <= closes:
            return ("poll", race_round, 15 * 60)
        if opens > now:
            upcoming = opens
            break
    if upcoming is None:
        return ("wait", None, 12 * 3600)
    seconds = (upcoming - now).total_seconds()
    return ("wait", None, min(max(seconds, 60), 6 * 3600))


def _has_winner(table: pd.DataFrame) -> bool:
    if table.empty or "positionText" not in table.columns:
        return False
    return (table["positionText"].astype(str) == "1").any()


def results_embed(response) -> discord.Embed:
    race = response.description.iloc[-1]
    table = response.content[-1].sort_values(by="position", na_position="last")
    lines = []
    for row in table.to_dict("records"):
        position = row.get("positionText") or "—"
        winner = str(position) == "1"
        lines.append(
            f"{str(position):>3}  {_code(row):<3}  {row['constructorName']} — {_result_text(row, winner=winner)}"
        )
    fastest = table[table["fastestLapRank"] == 1]
    footer = None
    if not fastest.empty:
        lap = fastest.iloc[0].to_dict()
        lap_time = lap.get("fastestLapTime")
        shown = ""
        if lap_time is not None and not pd.isna(lap_time):
            minutes, secs = divmod(lap_time.total_seconds(), 60)
            shown = f" {int(minutes)}:{secs:06.3f}"
        footer = f"Fastest lap: {_code(lap)}{shown}"

    when = race.raceDate.strftime("%d %b %Y")
    embed = discord.Embed(
        title=f"{race.raceName}",
        description=(
            f"Round {int(race['round'])} · {race.locality}, {race.country} · {when}\n"
            "```\n"
            + "\n".join(lines)
            + "\n```"
        ),
        color=F1_RED,
    )
    if footer:
        embed.set_footer(text=footer)
    return embed


class F1(commands.Cog):
    """Formula 1 schedule, standings, and results."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ergast = Ergast()
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = asyncio.Lock()

    async def cog_load(self):
        self._results_poster.start()

    async def cog_unload(self):
        self._results_poster.cancel()

    def _read_state(self) -> dict:
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_state(self, state: dict):
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = STATE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(STATE_PATH)

    def _results_channel_id(self) -> int | None:
        raw = os.getenv("F1_RESULTS_CHANNEL", "").strip()
        if raw.isdigit():
            return int(raw)
        channel_id = self._read_state().get("channel_id")
        if isinstance(channel_id, int):
            return channel_id
        return None

    def _already_posted(self, season: int, race_round: int) -> bool:
        posted = self._read_state().get("posted", [])
        return f"{season}-{race_round}" in posted

    def _mark_posted(self, season: int, race_round: int):
        state = self._read_state()
        posted = list(state.get("posted", []))
        key = f"{season}-{race_round}"
        if key not in posted:
            posted.append(key)
        state["posted"] = posted
        self._write_state(state)

    def _fetch_results_uncached(self, season: int, race_round: int):
        # An empty "not yet" response must not sit in the 12-hour HTTP cache.
        with Cache.disabled():
            return self._ergast.get_race_results(season=season, round=race_round)

    async def _check_for_race_result(self) -> float:
        channel_id = self._results_channel_id()
        if channel_id is None:
            log.info("Race results auto-post is off. Set it with !resultschannel in the target channel.")
            return 6 * 3600

        schedule = await self._load("schedule", self._season_schedule)
        starts: list[tuple[pd.Timestamp, int]] = []
        for event in schedule.itertuples(index=False):
            start = _race_start(event.Session5DateUtc)
            if start is None:
                continue
            starts.append((start, int(event.RoundNumber)))
        starts.sort()

        action, race_round, delay = poll_plan(starts, pd.Timestamp.now(tz="UTC"))
        if action != "poll" or race_round is None:
            when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=delay)
            log.info("Next race-result check around %s UTC", when.strftime("%Y-%m-%d %H:%M"))
            return delay

        season = datetime.datetime.now(datetime.timezone.utc).year
        if self._already_posted(season, race_round):
            return 30 * 60

        log.info("Checking whether round %s results are published", race_round)
        async with self._lock:
            response = await asyncio.to_thread(self._fetch_results_uncached, season, race_round)
        if response.description.empty or not response.content or not _has_winner(response.content[-1]):
            log.info("Round %s results are not published yet", race_round)
            return 15 * 60

        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        await channel.send(embed=results_embed(response))
        self._mark_posted(season, race_round)
        log.info("Posted round %s results to channel %s", race_round, channel_id)
        return 30 * 60

    @tasks.loop(minutes=30)
    async def _results_poster(self):
        delay = 30 * 60
        try:
            delay = await self._check_for_race_result()
        except Exception:
            log.exception("Race result check failed")
        self._results_poster.change_interval(seconds=max(60, int(delay)))

    @_results_poster.before_loop
    async def _before_results_poster(self):
        await self.bot.wait_until_ready()

    async def _load(self, key: str, fetch):
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None and time.monotonic() - cached[0] < CACHE_SECONDS:
                return cached[1]
            result = await asyncio.to_thread(fetch)
            self._cache[key] = (time.monotonic(), result)
            return result

    def _season_schedule(self):
        year = datetime.datetime.now(datetime.timezone.utc).year
        return fastf1.get_event_schedule(year, include_testing=False)

    @commands.command()
    async def f1(self, ctx: commands.Context):
        await ctx.send(
            "`!nextrace` — next race\n"
            "`!schedule` — remaining races (`full`, or a round number for the weekend)\n"
            "`!standings` — driver standings (`constructors` for teams)\n"
            "`!results` — last race (`!results 5` for a round)\n"
            "`!resultschannel` — post race results in this channel when the classification is published"
        )

    @commands.command()
    async def nextrace(self, ctx: commands.Context):
        await ctx.send("Searching for the next race via FastF1...")

        try:
            current_year = datetime.datetime.now().year
            current_time_ts = pd.Timestamp.now(tz="UTC")

            schedule = fastf1.get_event_schedule(current_year).copy()

            # FastF1 sometimes returns naive datetimes. Localize them so the
            # comparison with the current UTC time is valid.
            for col in ("Session4DateUtc", "Session5DateUtc"):
                if schedule[col].dt.tz is None:
                    schedule[col] = schedule[col].dt.tz_localize("UTC")

            race_schedule = schedule.loc[
                (schedule["EventFormat"].isin(["conventional", "sprint_qualifying"]))
                & (schedule["Session5DateUtc"] > current_time_ts)
            ]
            race_schedule = pd.concat(
                [
                    race_schedule,
                    schedule.loc[
                        (schedule["EventFormat"].isin(["sprint", "sprint_shootout"]))
                        & (schedule["Session4DateUtc"] > current_time_ts)
                    ],
                ]
            ).sort_values(by=["Session5DateUtc", "Session4DateUtc"]).iloc[0]

            race_name = race_schedule["EventName"]
            circuit_location = race_schedule["Location"]

            if race_schedule["EventFormat"] in ("conventional", "sprint_qualifying"):
                race_utc_time = race_schedule["Session5DateUtc"]
                track_local_time = race_schedule["Session5Date"].strftime("%H:%M %Z")
            elif race_schedule["EventFormat"] in ("sprint", "sprint_shootout"):
                race_utc_time = race_schedule["Session4DateUtc"]
                track_local_time = race_schedule["Session4Date"].strftime("%H:%M %Z")
            else:
                raise ValueError(
                    f"Unsupported event format or missing race time for: {race_name}"
                )

            unix_timestamp = int(race_utc_time.timestamp())
            race_time_display = f"<t:{unix_timestamp}:F>"

            embed = discord.Embed(
                title=f"Next Race: {race_name} Grand Prix",
                description=(
                    f"**Location:** {circuit_location}\n"
                    f"**Race Start Time:** {race_time_display}\n"
                ),
                color=F1_RED,
            )
            embed.add_field(
                name="Track Local Start Time",
                value=track_local_time,
                inline=False,
            )

            await ctx.send(embed=embed)
            print("Successfully sent embed via FastF1.")

        except IndexError:
            await ctx.send(
                "Error: Looks like the current F1 season has ended or the schedule is not available."
            )
        except Exception as e:
            print(f"FASTF1 ERROR: {type(e).__name__}: {e}")
            await ctx.send(
                f"An unexpected error occurred while processing the race data: `{type(e).__name__}`."
            )

    @commands.command()
    async def standings(self, ctx: commands.Context, category: str = "drivers"):
        """Driver or constructor championship standings."""
        kind = category.lower()
        if kind in {"constructor", "constructors", "teams", "wcc"}:
            loader = lambda: self._ergast.get_constructor_standings(season="current")
            build = constructor_standings_embed
            key = "constructor-standings"
        elif kind in {"driver", "drivers", "wdc"}:
            loader = lambda: self._ergast.get_driver_standings(season="current")
            build = driver_standings_embed
            key = "driver-standings"
        else:
            await ctx.send("Use `!standings` for drivers or `!standings constructors` for teams.")
            return

        try:
            async with ctx.typing():
                response = await self._load(key, loader)
                await ctx.send(embed=build(response))
        except Exception:
            log.exception("Failed to load standings")
            await ctx.send("Couldn't load the standings. Try again in a minute.")

    @commands.command()
    async def schedule(self, ctx: commands.Context, selection: str = "remaining"):
        """Remaining races, the full calendar, or one weekend."""
        choice = selection.lower()
        if choice not in {"remaining", "next", "full", "all", "season"} and not choice.isdigit():
            await ctx.send("Use `!schedule`, `!schedule full`, or `!schedule 5`.")
            return
        if choice == "next":
            choice = "remaining"

        try:
            async with ctx.typing():
                schedule = await self._load("schedule", self._season_schedule)
                await ctx.send(embed=schedule_embed(schedule, choice))
        except ValueError as exc:
            await ctx.send(str(exc))
        except Exception:
            log.exception("Failed to load the schedule")
            await ctx.send("Couldn't load the schedule. Try again in a minute.")

    @commands.command()
    async def results(self, ctx: commands.Context, race_round: str = "last"):
        """Classification for the last race, or for a round number."""
        choice = race_round.lower()
        if choice != "last" and not choice.isdigit():
            await ctx.send("Use `!results` for the last race, or `!results 5` for a round.")
            return
        query_round = "last" if choice == "last" else int(choice)

        def fetch():
            return self._ergast.get_race_results(season="current", round=query_round)

        try:
            async with ctx.typing():
                response = await self._load(f"results-{query_round}", fetch)
                if response.description.empty or not response.content:
                    await ctx.send(f"No result is available for round {choice}.")
                    return
                await ctx.send(embed=results_embed(response))
        except Exception:
            log.exception("Failed to load race results")
            await ctx.send("Couldn't load that race result. Try again in a minute.")

    @commands.command(name="resultschannel")
    @commands.has_guild_permissions(manage_guild=True)
    async def resultschannel(self, ctx: commands.Context):
        """Post future race classifications in this channel."""
        if os.getenv("F1_RESULTS_CHANNEL", "").strip():
            await ctx.send(
                "Automatic results are pinned to `F1_RESULTS_CHANNEL` in `.env`. "
                "Clear that value to choose a channel with this command."
            )
            return

        state = self._read_state()
        state["channel_id"] = ctx.channel.id
        self._write_state(state)
        await ctx.send(f"Race results will be posted in {ctx.channel.mention} after each race.")
        if self._results_poster.is_running():
            self._results_poster.restart()

    @resultschannel.error
    async def resultschannel_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("You need the Manage Server permission to choose the results channel.")
            return
        log.error("Failed to set the results channel", exc_info=error)


async def setup(bot: commands.Bot):
    await bot.add_cog(F1(bot))
