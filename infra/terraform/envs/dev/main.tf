terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Partial configuration. `manage.py` supplies bucket, key, and lock table so
  # the account id is never hardcoded in the repository.
  backend "s3" {}
}

provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "agentic-rag"
      Environment = "dev"
      ManagedBy   = "terraform"
      Owner       = var.owner
    }
  }
}

module "stack" {
  source = "../../stack"

  environment = "dev"
  project     = "agentic-rag"

  api_image = var.api_image
  ui_image  = var.ui_image

  # Dev runs without a NAT gateway. Tasks sit in public subnets with public IPs
  # so they can reach Supabase, OpenAI and ECR. This is the single largest
  # saving available in a small environment.
  enable_nat = false

  api_cpu           = 512
  api_memory        = 1024
  api_desired_count = 1
  ui_cpu            = 256
  ui_memory         = 512
  ui_desired_count  = 1

  log_level          = "DEBUG"
  log_retention_days = 30
  container_insights = false

  # Straight cutover: dev exists to find problems quickly, not to bake.
  deployment_config = "CodeDeployDefault.ECSAllAtOnce"
  bake_minutes      = 1

  alert_email       = var.alert_email
  github_repository = var.github_repository

  # The GitHub OIDC provider is account-wide and only one may exist. Dev
  # creates it; prod reuses it.
  create_github_role   = true
  create_oidc_provider = true
}
