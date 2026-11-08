variable "location" {
  type    = string
  default = "eastus"
}

variable "resource_group_name" {
  type    = string
  default = "rg-discord-bot"
}

variable "registry_name" {
  type        = string
  description = "Globally unique ACR name. Lowercase letters and numbers only."
  default     = "acrdiscordbotcody"

  validation {
    condition     = can(regex("^[a-z0-9]{5,50}$", var.registry_name))
    error_message = "registry_name must be 5-50 lowercase letters or numbers."
  }
}

variable "storage_account_name" {
  type        = string
  description = "Globally unique storage account name. Lowercase letters and numbers only."
  default     = "stdiscordbotcody"

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.storage_account_name))
    error_message = "storage_account_name must be 3-24 lowercase letters or numbers."
  }
}

variable "github_actions_principal_id" {
  type        = string
  description = "Object id of the GitHub Actions service principal created by the bootstrap stack."

  validation {
    condition     = can(regex("^[0-9a-fA-F-]{36}$", var.github_actions_principal_id))
    error_message = "github_actions_principal_id must be the bootstrap service principal object id."
  }
}

variable "f1_results_channel" {
  type        = string
  default     = ""
  description = "Discord channel id for automatic race posts. Empty leaves auto-post off until !resultschannel."
}

variable "image_tag" {
  type        = string
  default     = ""
  description = "Git commit sha already pushed to the registry. Leave empty to create the registry before the first image push."
}
