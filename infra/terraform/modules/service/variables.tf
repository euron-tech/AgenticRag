variable "name" {
  type        = string
  description = "Short service name, e.g. api or ui."
}

variable "name_prefix" {
  type = string
}

variable "region" {
  type = string
}

variable "image" {
  type        = string
  description = "Initial image. CodeDeploy replaces this on every deploy."
}

variable "container_port" {
  type = number
}

variable "prod_port" {
  type        = number
  description = "ALB listener carrying production traffic for this service."
}

variable "test_port" {
  type        = number
  description = "ALB listener the smoke test hits before traffic shifts."
}

variable "health_check_path" {
  type = string
}

variable "cpu" {
  type    = number
  default = 512
}

variable "memory" {
  type    = number
  default = 1024
}

variable "desired_count" {
  type    = number
  default = 1
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "bake_minutes" {
  type        = number
  default     = 5
  description = "How long the old version stays running after the shift, in case of rollback."
}

variable "wait_for_approval" {
  type        = bool
  default     = true
  description = "Hold at Ready until the smoke test passes and continue-deployment is called."
}

variable "approval_wait_minutes" {
  type        = number
  default     = 15
  description = "How long the deployment waits at Ready before giving up and rolling back."
}

variable "deployment_config" {
  type        = string
  default     = "CodeDeployDefault.ECSAllAtOnce"
  description = "CodeDeployDefault.ECSCanary10Percent5Minutes for a gradual prod shift."
}

variable "error_alarm_threshold" {
  type        = number
  default     = 5
  description = "5xx responses per minute that trigger rollback."
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "assign_public_ip" {
  type = bool
}

variable "alb_arn" {
  type = string
}

variable "alb_arn_suffix" {
  type = string
}

variable "alb_security_group_id" {
  type = string
}

variable "cluster_arn" {
  type = string
}

variable "cluster_name" {
  type = string
}

variable "execution_role_arn" {
  type = string
}

variable "task_role_arn" {
  type = string
}

variable "codedeploy_role_arn" {
  type = string
}

variable "environment" {
  type        = map(string)
  default     = {}
  description = "Plain environment variables. Never put a secret here."
}

variable "secrets" {
  type        = map(string)
  default     = {}
  description = "Environment variable name to Secrets Manager ARN."
}

variable "alarm_topic_arn" {
  type    = string
  default = ""
}
