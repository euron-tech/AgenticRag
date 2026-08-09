variable "project" {
  type    = string
  default = "agentic-rag"
}

variable "environment" {
  type        = string
  description = "dev or prod."
}

variable "region" {
  type        = string
  default     = "us-east-1"
  description = "Every resource in this project lives in us-east-1."

  validation {
    condition     = var.region == "us-east-1"
    error_message = "This project is us-east-1 only. A resource elsewhere is a bug."
  }
}

# ----------------------------------------------------------------- network
variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "enable_nat" {
  type        = bool
  default     = false
  description = "Private subnets + NAT gateway. Costs roughly $32/month when idle."
}

# ---------------------------------------------------------------- ingress
variable "ui_ingress_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "Who may reach the Streamlit UI."
}

variable "api_ingress_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = <<-EOT
    Who may reach the API listener. The UI container calls it from inside the
    VPC, but with a public ALB the source address is the task's public IP (no
    NAT) or the NAT gateway's EIP. In prod, set this to the NAT EIP /32. Every
    route except /health requires a valid JWT regardless.
  EOT
}

variable "test_ingress_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "Who may reach the blue/green test listeners. CI runners have arbitrary addresses."
}

# ---------------------------------------------------------------- services
variable "api_image" {
  type        = string
  description = "Initial API image. CodeDeploy replaces it on every deploy."
}

variable "ui_image" {
  type = string
}

variable "api_cpu" {
  type    = number
  default = 512
}

variable "api_memory" {
  type    = number
  default = 1024
}

variable "api_desired_count" {
  type    = number
  default = 1
}

variable "ui_cpu" {
  type    = number
  default = 256
}

variable "ui_memory" {
  type    = number
  default = 512
}

variable "ui_desired_count" {
  type    = number
  default = 1
}

# --------------------------------------------------------------- behaviour
variable "log_level" {
  type    = string
  default = "INFO"
}

variable "chat_model" {
  type    = string
  default = "gpt-4o-mini"
}

variable "embedding_model" {
  type        = string
  default     = "text-embedding-3-small"
  description = "Changing this requires a schema migration and a full re-index."
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "bake_minutes" {
  type        = number
  default     = 5
  description = "How long the previous version stays alive after traffic shifts."
}

variable "deployment_config" {
  type    = string
  default = "CodeDeployDefault.ECSAllAtOnce"
}

variable "container_insights" {
  type    = bool
  default = false
}

variable "enable_deletion_protection" {
  type        = bool
  default     = false
  description = "True blocks `manage.py destroy` until it is turned off by hand."
}

variable "latency_p99_seconds" {
  type    = number
  default = 15
}

variable "alert_email" {
  type    = string
  default = ""
}

# ------------------------------------------------------------------ github
variable "create_github_role" {
  type    = bool
  default = true
}

variable "create_oidc_provider" {
  type        = bool
  default     = true
  description = "Only one GitHub OIDC provider may exist per account. Set false for the second environment."
}

variable "github_repository" {
  type    = string
  default = "owner/repo"
}
