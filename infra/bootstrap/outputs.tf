output "storage_account_name" {
  value = azurerm_storage_account.state.name
}

output "resource_group_name" {
  value = azurerm_resource_group.state.name
}

output "client_id" {
  value = azuread_application.github.client_id
}

output "tenant_id" {
  value = data.azurerm_client_config.current.tenant_id
}

output "subscription_id" {
  value = data.azurerm_client_config.current.subscription_id
}

output "principal_id" {
  value = azuread_service_principal.github.object_id
}
