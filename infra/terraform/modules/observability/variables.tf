variable "name_prefix" {
  type = string
}

variable "environment" {
  type = string
}

variable "region" {
  type = string
}

variable "alb_arn_suffix" {
  type = string
}

variable "log_groups" {
  type        = map(string)
  description = "Service name to CloudWatch log group name."
}

variable "alert_topic_arn" {
  type        = string
  description = "SNS topic these alarms notify. Created by the stack."
}

variable "error_log_threshold" {
  type    = number
  default = 10
}

variable "ingestion_failure_threshold" {
  type    = number
  default = 3
}

variable "latency_p99_seconds" {
  type    = number
  default = 10
}
