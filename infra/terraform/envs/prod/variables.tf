variable "api_image" {
  type        = string
  description = "Set by manage.py after the images are pushed."
}

variable "ui_image" {
  type = string
}

variable "owner" {
  type    = string
  default = "platform"
}

variable "alert_email" {
  type    = string
  default = ""
}

variable "github_repository" {
  type    = string
  default = "owner/repo"
}

variable "api_ingress_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = <<-EOT
    Narrow this to the NAT gateway's public IP (see the `egress_cidrs` output)
    once the environment exists. Left open, the API listener is reachable from
    the internet — still JWT-protected, but needlessly exposed.
  EOT
}
