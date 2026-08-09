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
  type        = string
  default     = ""
  description = "Where CloudWatch alarms are sent. Requires confirming an email from AWS."
}

variable "github_repository" {
  type        = string
  default     = "owner/repo"
  description = "owner/repo permitted to assume the deploy role."
}
