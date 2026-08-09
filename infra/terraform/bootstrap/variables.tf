variable "project" {
  type        = string
  default     = "agentic-rag"
  description = "Prefix for all resource names."
}

variable "owner" {
  type        = string
  default     = "platform"
  description = "Owner tag applied to every resource."
}
