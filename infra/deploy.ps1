# Bring the bot up on Azure. This PC can be off after the script finishes.
#
# Order:
#   1. Write gitignored terraform.tfvars. The Discord token stays in Key Vault.
#   2. terraform apply with no image tag. Creates the resource group, private
#      registry, file share, and Container Apps environment. The app is skipped
#      until an image exists.
#   3. Upload data/f1_posted.json so a new container does not repeat a race post.
#   4. publish.ps1 builds the image with local Docker and pushes the git sha tag.
#   5. Record that sha in terraform.tfvars and apply again. That creates one
#      replica and no ingress. The tag stays in the gitignored tfvars so a
#      later apply does not delete the app.

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

function ConvertTo-HclString([string]$Value) {
    return '"' + $Value.Replace('\', '\\').Replace('"', '\"') + '"'
}

$envPath = Join-Path $PSScriptRoot "..\.env"
if (-not (Test-Path $envPath)) {
    Write-Error ".env is missing."
}

$values = @{}
foreach ($line in Get-Content $envPath) {
    if ($line -match '^\s*#' -or $line -notmatch '=') {
        continue
    }
    $name, $value = $line.Split('=', 2)
    $value = $value.Trim()
    if ($value.Length -ge 2 -and $value.StartsWith('"') -and $value.EndsWith('"')) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    $values[$name.Trim()] = $value
}

$channel = $values["F1_RESULTS_CHANNEL"]
if (-not $channel) {
    $channel = ""
}

$existingTag = ""
$tagFile = Join-Path $PSScriptRoot ".image-tag"
if (Test-Path $tagFile) {
    $existingTag = (Get-Content -Path $tagFile -Raw).ToString().Trim()
}

$principal = (terraform -chdir=bootstrap output -raw principal_id).ToString().Trim()
if (-not $principal) {
    Write-Error "Bootstrap principal id is missing. Apply infra/bootstrap first."
}

$tfvars = "f1_results_channel          = $(ConvertTo-HclString $channel)`n" +
          "image_tag                   = $(ConvertTo-HclString $existingTag)`n" +
          "github_actions_principal_id = $(ConvertTo-HclString $principal)`n"
[System.IO.File]::WriteAllText((Join-Path $PSScriptRoot "terraform.tfvars"), $tfvars)
Write-Host "Wrote terraform.tfvars"

terraform init -input=false
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

terraform apply -input=false -auto-approve
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$account = (terraform output -raw storage_account_name).ToString().Trim()
$share = (terraform output -raw file_share_name).ToString().Trim()
$group = (terraform output -raw resource_group_name).ToString().Trim()
$key = (az storage account keys list --resource-group $group --account-name $account --query "[0].value" -o tsv).ToString().Trim()
if (-not $key) {
    Write-Error "Could not read the storage account key."
}

$stateFile = Join-Path $PSScriptRoot "..\data\f1_posted.json"
if (-not (Test-Path $stateFile)) {
    Write-Error "data/f1_posted.json is missing."
}

az storage file upload --account-name $account --share-name $share --source $stateFile --path "f1_posted.json" --account-key $key --only-show-errors
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Uploaded f1_posted.json to $share"

& .\publish.ps1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$sha = (Get-Content -Path (Join-Path $PSScriptRoot ".image-tag") -Raw).ToString().Trim()
if (-not $sha) {
    Write-Error "Image publish did not record a tag."
}

$tfvars = "f1_results_channel          = $(ConvertTo-HclString $channel)`n" +
          "image_tag                   = $(ConvertTo-HclString $sha)`n" +
          "github_actions_principal_id = $(ConvertTo-HclString $principal)`n"
[System.IO.File]::WriteAllText((Join-Path $PSScriptRoot "terraform.tfvars"), $tfvars)

terraform apply -input=false -auto-approve
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Container app is set to image tag $sha"
