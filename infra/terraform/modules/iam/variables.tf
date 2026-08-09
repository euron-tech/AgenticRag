variable "name_prefix" {
  type = string
}

variable "secret_arns" {
  type        = list(string)
  description = "Secrets Manager ARNs the execution role may read."
}

variable "create_github_role" {
  type        = bool
  default     = true
  description = "Create the OIDC deploy role for GitHub Actions."
}

variable "create_oidc_provider" {
  type        = bool
  default     = true
  description = "False if the account already has the GitHub OIDC provider (only one is allowed per account)."
}

variable "github_repository" {
  type        = string
  default     = "owner/repo"
  description = "owner/repo allowed to assume the deploy role."
}
