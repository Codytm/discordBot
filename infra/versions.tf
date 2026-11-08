terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }

  backend "azurerm" {
    resource_group_name  = "rg-discord-bot-tfstate"
    storage_account_name = "stdiscordbottfstate"
    container_name       = "tfstate"
    key                  = "discord-bot.tfstate"
    use_azuread_auth     = true
  }
}

provider "azurerm" {
  features {}
  # Subscription comes from ARM_SUBSCRIPTION_ID. deploy.ps1 sets that from
  # the current Azure CLI account so the id is not stored in this public repo.
  resource_provider_registrations = "none"
  resource_providers_to_register = [
    "Microsoft.App",
    "Microsoft.ContainerRegistry",
    "Microsoft.OperationalInsights",
    "Microsoft.Storage",
    "Microsoft.KeyVault",
  ]
}
