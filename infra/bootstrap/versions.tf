# Applied once from this PC before GitHub Actions exists.
# This stack's own state stays local and gitignored. The main stack's
# state is what goes into the storage account created here.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  features {}
  resource_provider_registrations = "none"
  resource_providers_to_register  = ["Microsoft.Storage"]
}

provider "azuread" {}
