variable "name_prefix" {
  type        = string
  description = "Prefix for resource names, e.g. agentic-rag-dev."
}

variable "cidr_block" {
  type        = string
  default     = "10.20.0.0/16"
  description = "VPC CIDR."
}

variable "enable_nat" {
  type        = bool
  default     = false
  description = "Private subnets with a NAT gateway. False puts tasks in public subnets."
}
