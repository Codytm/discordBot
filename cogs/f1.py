import datetime

import discord
from discord.ext import commands
import fastf1
import pandas as pd


class F1(commands.Cog):
    """Formula 1 lookups."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command()
    async def f1(self, ctx: commands.Context):
        await ctx.send(f"f1 {ctx.author.mention}")

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
                color=0xE10600,
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


async def setup(bot: commands.Bot):
    await bot.add_cog(F1(bot))
