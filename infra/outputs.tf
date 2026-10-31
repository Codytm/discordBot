output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "registry_name" {
  value = azurerm_container_registry.this.name
}

output "registry_login_server" {
  value = azurerm_container_registry.this.login_server
}

output "storage_account_name" {
  value = azurerm_storage_account.this.name
}

output "file_share_name" {
  value = azurerm_storage_share.this.name
}

output "container_app_name" {
  value = try(azurerm_container_app.this[0].name, "")
}
