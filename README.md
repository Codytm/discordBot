# Discord bot

A Discord bot for a Formula 1 server. It answers race questions, posts results after a race, and fixes X links so Discord shows the media. One copy runs on Azure Container Apps, so the PC that built it does not have to stay on.

The command prefix is `!`.

## What it does

`cogs/general.py` welcomes a new member with a direct message, replies to a few running jokes, and answers `!hello`.

`cogs/f1.py` loads the season from FastF1 and the Ergast API. Standings, the calendar, and race classifications are sent as embeds. After a race, a background loop checks whether the classification and the driver standings are published, then posts each one once. The record of what was already posted is `data/f1_posted.json`.

`cogs/link_fix.py` finds `x.com` and `twitter.com` status links and reposts them through FxEmbed (`fixupx.com` / `fxtwitter.com`). Discord's own X embeds drop images and video. The repost uses a webhook named `EmbedFix` so the message still shows the original author, then the original message is removed. Links inside code spans or spoilers are left alone. The bot needs Manage Webhooks in that channel.

## Commands

| Command | What it does |
| --- | --- |
| `!hello` | Greets the person who sent it. |
| `!f1` | Lists the race commands. |
| `!nextrace` | Next race, with a Discord timestamp and the track-local start time. |
| `!schedule` | Races still to come. `!schedule full` is the season. `!schedule 5` is one weekend. |
| `!standings` | Driver championship. `!standings constructors` is the team championship. |
| `!results` | Last race classification. `!results 5` is a round number. |
| `!resultschannel` | Posts future results in this channel. Requires Manage Server. Ignored when `F1_RESULTS_CHANNEL` is set. |

## What it is built with

- Python 3.12
- [discord.py](https://discordpy.readthedocs.io/) for the gateway, commands, and embeds
- [FastF1](https://docs.fastf1.dev/) and pandas for the schedule and session times
- FastF1's Ergast client for standings and race results
- python-dotenv for local configuration

The bot needs the message content intent and the server members intent.

## How it runs on Azure

Terraform in `infra/` creates the resources in `eastus`. `infra/deploy.ps1` is the apply order.

| Piece | Why it is there |
| --- | --- |
| Resource group `rg-discord-bot` | One boundary for the bill and for `terraform destroy`. |
| Azure Container Registry, Basic, admin login off | Holds the image. Anonymous pull is not available on this SKU. This PC pushes with `az acr login`. The app pulls with a user-assigned identity that only has `AcrPull` on this registry. |
| Storage account and the `botdata` file share | Keeps `f1_posted.json` across restarts so a race is not announced twice. Anonymous access is off. HTTPS only, TLS 1.2. |
| Log Analytics workspace | Container Apps environment log destination. The bot itself does not write a log file. |
| Container Apps environment and `ca-discord-bot` | Runs the process. One replica, because a second login would double every reply. No ingress, because nothing on the internet connects in. The bot dials out to Discord and FastF1. |
| 0.5 vCPU and 1 GiB | The consumption plan pairs 1 GiB with 0.5 vCPU. pandas and FastF1 need that memory. |

Docker on this PC is only used to build and push the image. The image tag is the git commit SHA, so a new push rolls a new revision. After `deploy.ps1` finishes, this PC and Docker can be off.

The subscription id is not in the repo. Terraform uses the Azure CLI login through `ARM_SUBSCRIPTION_ID`, which `deploy.ps1` sets.

## Run it locally

```powershell
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

Create a `.env` file in the repo root with `DISCORD_TOKEN`. `F1_RESULTS_CHANNEL` is optional. When it is set, automatic race posts go to that channel and `!resultschannel` will not change it.

## Deploy it

From `infra/`, with the Azure CLI logged in, Terraform, Docker, and Git installed:

```powershell
.\deploy.ps1
```

The script writes `infra/terraform.tfvars` from `.env`, applies the registry and file share, uploads `data/f1_posted.json`, builds and pushes the image, then applies the container app.

A later code change is another `.\deploy.ps1`. That builds a new SHA tag and updates the app.

## What is not on GitHub

`.env`, `infra/terraform.tfvars`, and `infra/terraform.tfstate` hold the Discord token and the storage account key. They are gitignored. `infra/terraform.tfvars.example` shows the variable names with empty values.
