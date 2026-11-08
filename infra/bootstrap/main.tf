data "azurerm_client_config" "current" {}

data "azurerm_resource_group" "bot" {
  name = "rg-discord-bot"
}

data "azurerm_container_registry" "bot" {
  name                = "acrdiscordbotcody"
  resource_group_name = data.azurerm_resource_group.bot.name
}

resource "azurerm_resource_group" "state" {
  name     = "rg-discord-bot-tfstate"
  location = "eastus"
}

resource "azurerm_storage_account" "state" {
  name                            = "stdiscordbottfstate"
  resource_group_name             = azurerm_resource_group.state.name
  location                        = azurerm_resource_group.state.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  # The provider checks the blob service with a key while it creates the
  # account. The Terraform backend still uses Azure AD, so the workflow
  # never reads this key.
  shared_access_key_enabled = true

  blob_properties {
    versioning_enabled = true
  }
}

resource "azurerm_storage_container" "tfstate" {
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
}

resource "azuread_application" "github" {
  display_name = "github-discord-bot"
}

resource "azuread_service_principal" "github" {
  client_id = azuread_application.github.client_id
}

resource "azuread_application_federated_identity_credential" "pull_request" {
  application_id = azuread_application.github.id
  display_name   = "github-pull-request"
  description    = "Plans from pull requests in Codytm/discordBot."
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = "https://token.actions.githubusercontent.com"
  # This repo includes the owner and repository ids in the Actions OIDC subject.
  subject        = "repo:Codytm@120184933/discordBot@1426336156:pull_request"
}

resource "azuread_application_federated_identity_credential" "main" {
  application_id = azuread_application.github.id
  display_name   = "github-main"
  description    = "Applies pushes to main. The production environment sets this subject."
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = "https://token.actions.githubusercontent.com"
  subject        = "repo:Codytm@120184933/discordBot@1426336156:environment:production"
}

resource "azurerm_role_assignment" "state_ci" {
  scope                            = azurerm_storage_account.state.id
  role_definition_name             = "Storage Blob Data Contributor"
  principal_id                     = azuread_service_principal.github.object_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "state_operator" {
  scope                = azurerm_storage_account.state.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "bot_contributor" {
  scope                            = data.azurerm_resource_group.bot.id
  role_definition_name             = "Contributor"
  principal_id                     = azuread_service_principal.github.object_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "bot_rbac" {
  scope                            = data.azurerm_resource_group.bot.id
  role_definition_name             = "Role Based Access Control Administrator"
  principal_id                     = azuread_service_principal.github.object_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "acr_push" {
  scope                            = data.azurerm_container_registry.bot.id
  role_definition_name             = "AcrPush"
  principal_id                     = azuread_service_principal.github.object_id
  skip_service_principal_aad_check = true
}
