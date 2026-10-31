# Build the bot image and push it to the private registry.
# The registry must already exist. deploy.ps1 runs the full order.
# Docker is only needed for this push. Azure runs the container after that.

param()

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

foreach ($command in @("terraform", "az", "docker", "git")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        Write-Error "Required command not found: $command"
    }
}

$subscriptionId = (az account show --query id -o tsv).ToString().Trim()
if (-not $subscriptionId) {
    Write-Error "Azure CLI is not logged in."
}
$env:ARM_SUBSCRIPTION_ID = $subscriptionId

terraform init -input=false
if ($LASTEXITCODE -ne 0) { throw "terraform init failed" }

$loginServer = (terraform output -raw registry_login_server).ToString().Trim()
$registryName = (terraform output -raw registry_name).ToString().Trim()
if (-not $loginServer -or -not $registryName) {
    Write-Error "Registry outputs are empty. Apply the registry before publishing."
}

az acr login --name $registryName
if ($LASTEXITCODE -ne 0) { throw "az acr login failed" }

$sha = (git -C .. rev-parse HEAD).ToString().Trim()
$image = "${loginServer}/discordbot:${sha}"

docker build -t $image ..
if ($LASTEXITCODE -ne 0) { throw "docker build failed" }

docker push $image
if ($LASTEXITCODE -ne 0) { throw "docker push failed" }

Set-Content -Path (Join-Path $PSScriptRoot ".image-tag") -Value $sha -Encoding ascii -NoNewline
Write-Host "Pushed $image"
