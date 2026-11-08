# GitHub Actions plans this stack on pull requests and applies it on main.
# infra/bootstrap is applied once from this PC and is not part of that pipeline.
# The Discord token is read from Key Vault. image_tag must already exist in the registry.

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_container_registry" "this" {
  name                = var.registry_name
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "Basic"
  # No registry password. This PC pushes with az acr login. The app pulls
  # with its managed identity. Basic SKU does not allow anonymous pull.
  admin_enabled = false
}

resource "azurerm_storage_account" "this" {
  name                            = var.storage_account_name
  resource_group_name             = azurerm_resource_group.this.name
  location                        = azurerm_resource_group.this.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = true
}

resource "azurerm_storage_share" "this" {
  name               = "botdata"
  storage_account_id = azurerm_storage_account.this.id
  quota              = 1
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "log-discord-bot"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

resource "azurerm_container_app_environment" "this" {
  name                       = "cae-discord-bot"
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  # Azure adds this profile by itself. Declaring it keeps the next apply
  # from trying to delete it.
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
    minimum_count         = 0
    maximum_count         = 0
  }
}

resource "azurerm_container_app_environment_storage" "this" {
  name                         = "botdata"
  container_app_environment_id = azurerm_container_app_environment.this.id
  account_name                 = azurerm_storage_account.this.name
  share_name                   = azurerm_storage_share.this.name
  access_key                   = azurerm_storage_account.this.primary_access_key
  access_mode                  = "ReadWrite"
}

data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "this" {
  name                          = "kv-discordbotcody"
  location                      = azurerm_resource_group.this.location
  resource_group_name           = azurerm_resource_group.this.name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  public_network_access_enabled = true
  purge_protection_enabled      = false
}

resource "azurerm_role_assignment" "key_vault_ci" {
  scope                            = azurerm_key_vault.this.id
  role_definition_name             = "Key Vault Secrets User"
  principal_id                     = var.github_actions_principal_id
  skip_service_principal_aad_check = true
}

data "azurerm_key_vault_secret" "discord_token" {
  name         = "discord-token"
  key_vault_id = azurerm_key_vault.this.id

  depends_on = [azurerm_role_assignment.key_vault_ci]
}

resource "azurerm_user_assigned_identity" "this" {
  name                = "id-discord-bot"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                            = azurerm_container_registry.this.id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_user_assigned_identity.this.principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_container_app" "this" {
  count = var.image_tag == "" ? 0 : 1

  name                         = "ca-discord-bot"
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.this.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.this.id
  }

  secret {
    name  = "discord-token"
    value = data.azurerm_key_vault_secret.discord_token.value
  }

  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name  = "bot"
      image = "${azurerm_container_registry.this.login_server}/discordbot:${var.image_tag}"
      # Consumption plan pairs 1Gi with 0.5 vCPU. 0.25 vCPU only allows 0.5Gi,
      # which is tight once pandas and fastf1 are imported.
      cpu    = 0.5
      memory = "1Gi"

      env {
        name        = "DISCORD_TOKEN"
        secret_name = "discord-token"
      }

      env {
        name  = "F1_RESULTS_CHANNEL"
        value = var.f1_results_channel
      }

      volume_mounts {
        name = "botdata"
        path = "/app/data"
      }
    }

    volume {
      name         = "botdata"
      storage_name = azurerm_container_app_environment_storage.this.name
      storage_type = "AzureFile"
    }
  }

  depends_on = [azurerm_role_assignment.acr_pull]
}
