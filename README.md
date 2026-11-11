# Discord bot

This is a Discord bot that I created for my friends and I. I will continue to implement functions as needed or requested by my friends, but I use the project to practice infrastructure work I want to do professionally: packaging an app, deploying it with Terraform, and keeping it running on Azure. It answers race questions, posts results after a race, and fixes X links so Discord shows the media. One copy runs on Azure Container Apps.

The command prefix is `!`.

## What it does

`cogs/general.py` welcomes a new member with a direct message, replies to a few running jokes, and answers `!hello`.

`cogs/f1.py` loads the season from FastF1 and the Ergast API. Standings, the calendar, and race classifications are sent as embeds. After a race, a background loop checks whether the classification and the driver standings are published, then posts each one once. The record of what was already posted is `data/f1_posted.json`.

`cogs/link_fix.py` finds `x.com` and `twitter.com` status links and reposts them through FxEmbed (`fixupx.com` / `fxtwitter.com`). Discord's own X embeds drop images and video. The repost uses a webhook named `EmbedFix` so the message still shows the original author, then the original message is removed. Links inside code spans or spoilers are left alone. The bot needs Manage Webhooks in that channel.

`cogs/music.py` joins a voice channel and plays one YouTube track at a time. A search or a link is resolved with yt-dlp, and ffmpeg streams the audio. The queue lives in memory, so a restart clears it. The bot's role needs Connect and Speak.

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
| `!join` | Joins the voice channel you are in. |
| `!play` | Queues a YouTube link or search and starts playback when nothing else is playing. |
| `!skip` | Skips the current track. |
| `!stop` | Stops playback and clears the queue. |
| `!queue` | Shows the current track and the queue. |
| `!leave` | Clears the queue and leaves the voice channel. |

## What it is built with

- Python 3.12
- [discord.py](https://discordpy.readthedocs.io/) for the gateway, commands, and embeds
- [FastF1](https://docs.fastf1.dev/) and pandas for the schedule and session times
- FastF1's Ergast client for standings and race results
- python-dotenv for local configuration
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) and ffmpeg for YouTube audio in a voice channel
- PyNaCl and davey, installed with `discord.py[voice]`, for Discord voice encryption

The bot needs the message content intent and the server members intent. YouTube playback depends on yt-dlp and can break when YouTube changes its player. Age-restricted and live videos are not played. Local runs need ffmpeg on `PATH`. The container image installs it.

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
| Key Vault `kv-discordbotcody` | Holds the Discord token. The container reads it at deploy time. Anonymous access is off. |
| Resource group `rg-discord-bot-tfstate` and storage account `stdiscordbottfstate` | Remote Terraform state, separate from the bot so destroying the bot does not delete state. The container is private, versioning is on, and the workflow authenticates with Azure AD instead of a storage key. |

![Terraform](https://github.com/Codytm/discordBot/actions/workflows/terraform.yml/badge.svg)

## Continuous delivery

GitHub Actions plans this stack on a pull request and applies it after the pull request merges to `main`. The workflow is [.github/workflows/terraform.yml](.github/workflows/terraform.yml). Terraform is pinned to 1.16.6.

GitHub signs in to Azure with OpenID Connect. The app registration is `github-discord-bot`, created by the one-time [infra/bootstrap](infra/bootstrap) stack. A pull request from a fork does not get that login. Apply runs only on a push to `main`, in a GitHub Environment named `production`.

On `main`, the workflow builds the image, pushes `discordbot:<commit sha>` to the private registry, then applies Terraform with that tag. A pull request plans against the image tag that is already running, so the plan does not propose deleting the app.

These GitHub Actions variables are identifiers, not the Discord token:

| Variable | Source |
| --- | --- |
| `AZURE_CLIENT_ID` | `terraform -chdir=infra/bootstrap output -raw client_id` |
| `AZURE_TENANT_ID` | `terraform -chdir=infra/bootstrap output -raw tenant_id` |
| `AZURE_SUBSCRIPTION_ID` | `terraform -chdir=infra/bootstrap output -raw subscription_id` |
| `AZURE_GITHUB_PRINCIPAL_ID` | `terraform -chdir=infra/bootstrap output -raw principal_id` |
| `F1_RESULTS_CHANNEL` | The same channel id as in `.env` |

From the repo root, with the GitHub CLI logged in:

```powershell
gh variable set AZURE_CLIENT_ID --body (terraform -chdir=infra/bootstrap output -raw client_id)
gh variable set AZURE_TENANT_ID --body (terraform -chdir=infra/bootstrap output -raw tenant_id)
gh variable set AZURE_SUBSCRIPTION_ID --body (terraform -chdir=infra/bootstrap output -raw subscription_id)
gh variable set AZURE_GITHUB_PRINCIPAL_ID --body (terraform -chdir=infra/bootstrap output -raw principal_id)
gh variable set F1_RESULTS_CHANNEL --body "<channel id from .env>"
```

The bootstrap stack is applied once from this PC, before the workflow exists. Its own state stays local. After that storage account exists, the main stack's backend is the private container, and the existing state is moved with `terraform init -migrate-state`.

Turn on branch protection for `main` and require the Terraform workflow before merge. That setting lives in GitHub, not in Terraform.

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

The script writes `infra/terraform.tfvars` from `.env` and the bootstrap output, applies the registry and file share, uploads `data/f1_posted.json`, builds and pushes the image, then applies the container app. It does not write the Discord token into Terraform. A new or rotated token is stored with `az keyvault secret set` in `kv-discordbotcody`.

A later code change can be another `.\deploy.ps1`, or a merge to `main`, which runs the GitHub Actions workflow.

## What is not on GitHub

`.env` holds the Discord token for local runs. `infra/terraform.tfvars` holds the image tag, the results channel, and the GitHub principal id. `infra/bootstrap/terraform.tfstate` is the bootstrap stack's local state. All of those are gitignored. `infra/terraform.tfvars.example` shows the variable names with empty values. The live token and the main stack's state are in Azure, in Key Vault and the private state container.
