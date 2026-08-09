terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {}
}

provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "agentic-rag"
      Environment = "prod"
      ManagedBy   = "terraform"
      Owner       = var.owner
    }
  }
}

module "stack" {
  source = "../../stack"

  environment = "prod"
  project     = "agentic-rag"
  vpc_cidr    = "10.30.0.0/16"

  api_image = var.api_image
  ui_image  = var.ui_image

  # Private subnets behind a NAT gateway.
  enable_nat = true

  api_cpu           = 1024
  api_memory        = 2048
  api_desired_count = 2
  ui_cpu            = 512
  ui_memory         = 1024
  ui_desired_count  = 2

  log_level          = "INFO"
  log_retention_days = 90
  container_insights = true

  # Shift 10% of traffic, wait five minutes, then the rest. Keep the old
  # version alive for ten minutes so a rollback is instant.
  deployment_config = "CodeDeployDefault.ECSCanary10Percent5Minutes"
  bake_minutes      = 10

  # After the first apply, read `egress_cidrs` from the dev/prod outputs and put
  # the NAT gateway's address here, so the API listener stops being open to the
  # internet. It cannot be set before the NAT gateway exists.
  api_ingress_cidrs = var.api_ingress_cidrs

  latency_p99_seconds = 12
  alert_email         = var.alert_email
  github_repository   = var.github_repository

  # Dev created the account-wide OIDC provider; prod reuses it.
  create_github_role   = true
  create_oidc_provider = false
}
