# The whole environment. `envs/dev` and `envs/prod` differ only in variables.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

locals {
  name_prefix = "${var.project}-${var.environment}"

  # Four listeners on one load balancer. The UI is public on :80; the API is a
  # separate listener rather than a path rule, because CodeDeploy blue/green
  # swaps a listener's default action and does not manage path rules.
  ports = {
    ui_prod  = 80
    ui_test  = 8081
    api_prod = 8080
    api_test = 8082
  }

  secret_keys = [
    "OPENAI_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_JWT_SECRET",
    "SUPABASE_DB_URL",
  ]
}

# ------------------------------------------------------------------ alerts
resource "aws_sns_topic" "alerts" {
  name = "${local.name_prefix}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
  # AWS emails a confirmation link. Until someone clicks it the subscription is
  # pending and delivers nothing.
}

# ----------------------------------------------------------------- secrets
# Terraform creates the containers; the values are written by
# `python manage.py secrets --env <env>`. Putting a secret value in a Terraform
# variable would write it into state in plaintext.
resource "aws_secretsmanager_secret" "app" {
  for_each = toset(local.secret_keys)

  name = "${local.name_prefix}/${each.value}"
  # 0 means an immediate delete instead of a 7-day recovery window. Without it,
  # destroying and recreating an environment fails: the name is still reserved.
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "placeholder" {
  for_each = aws_secretsmanager_secret.app

  secret_id     = each.value.id
  secret_string = "PLACEHOLDER-set-with-manage.py-secrets"

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# ----------------------------------------------------------------- network
module "network" {
  source = "../modules/network"

  name_prefix = local.name_prefix
  cidr_block  = var.vpc_cidr
  enable_nat  = var.enable_nat
}

module "iam" {
  source = "../modules/iam"

  name_prefix          = local.name_prefix
  secret_arns          = [for s in aws_secretsmanager_secret.app : s.arn]
  create_github_role   = var.create_github_role
  create_oidc_provider = var.create_oidc_provider
  github_repository    = var.github_repository
}

# --------------------------------------------------------------------- alb
resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb"
  description = "Public entry point"
  vpc_id      = module.network.vpc_id

  ingress {
    description = "Streamlit UI"
    from_port   = local.ports.ui_prod
    to_port     = local.ports.ui_prod
    protocol    = "tcp"
    cidr_blocks = var.ui_ingress_cidrs
  }

  ingress {
    description = "API. The UI container calls this listener."
    from_port   = local.ports.api_prod
    to_port     = local.ports.api_prod
    protocol    = "tcp"
    cidr_blocks = var.api_ingress_cidrs
  }

  ingress {
    description = "Blue/green test listeners, hit by the deploy smoke test"
    from_port   = local.ports.ui_test
    to_port     = local.ports.ui_test
    protocol    = "tcp"
    cidr_blocks = var.test_ingress_cidrs
  }

  ingress {
    description = "Blue/green test listener for the API"
    from_port   = local.ports.api_test
    to_port     = local.ports.api_test
    protocol    = "tcp"
    cidr_blocks = var.test_ingress_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name_prefix}-alb" }
}

resource "aws_lb" "this" {
  name               = substr("${local.name_prefix}-alb", 0, 32)
  load_balancer_type = "application"
  internal           = false
  subnets            = module.network.public_subnet_ids
  security_groups    = [aws_security_group.alb.id]

  # Left off so `manage.py destroy` completes without a manual console step.
  enable_deletion_protection = var.enable_deletion_protection
  idle_timeout               = 300 # long enough for a slow agent turn
}

resource "aws_ecs_cluster" "this" {
  name = "${local.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = var.container_insights ? "enabled" : "disabled"
  }
}

# ---------------------------------------------------------------- services
module "api" {
  source = "../modules/service"

  name        = "api"
  name_prefix = local.name_prefix
  region      = var.region

  image             = var.api_image
  container_port    = 8000
  prod_port         = local.ports.api_prod
  test_port         = local.ports.api_test
  health_check_path = "/health"

  cpu                = var.api_cpu
  memory             = var.api_memory
  desired_count      = var.api_desired_count
  log_retention_days = var.log_retention_days
  bake_minutes       = var.bake_minutes
  deployment_config  = var.deployment_config

  vpc_id                = module.network.vpc_id
  subnet_ids            = module.network.task_subnet_ids
  assign_public_ip      = module.network.assign_public_ip
  alb_arn               = aws_lb.this.arn
  alb_arn_suffix        = aws_lb.this.arn_suffix
  alb_security_group_id = aws_security_group.alb.id

  cluster_arn         = aws_ecs_cluster.this.arn
  cluster_name        = aws_ecs_cluster.this.name
  execution_role_arn  = module.iam.execution_role_arn
  task_role_arn       = module.iam.task_role_arn
  codedeploy_role_arn = module.iam.codedeploy_role_arn
  alarm_topic_arn     = aws_sns_topic.alerts.arn

  environment = {
    APP_ENV         = var.environment
    LOG_LEVEL       = var.log_level
    STORAGE_BUCKET  = "documents"
    CHAT_MODEL      = var.chat_model
    EMBEDDING_MODEL = var.embedding_model
  }

  secrets = { for k, s in aws_secretsmanager_secret.app : k => s.arn }
}

module "ui" {
  source = "../modules/service"

  name        = "ui"
  name_prefix = local.name_prefix
  region      = var.region

  image             = var.ui_image
  container_port    = 8501
  prod_port         = local.ports.ui_prod
  test_port         = local.ports.ui_test
  health_check_path = "/_stcore/health"

  cpu                = var.ui_cpu
  memory             = var.ui_memory
  desired_count      = var.ui_desired_count
  log_retention_days = var.log_retention_days
  bake_minutes       = var.bake_minutes
  deployment_config  = var.deployment_config

  vpc_id                = module.network.vpc_id
  subnet_ids            = module.network.task_subnet_ids
  assign_public_ip      = module.network.assign_public_ip
  alb_arn               = aws_lb.this.arn
  alb_arn_suffix        = aws_lb.this.arn_suffix
  alb_security_group_id = aws_security_group.alb.id

  cluster_arn         = aws_ecs_cluster.this.arn
  cluster_name        = aws_ecs_cluster.this.name
  execution_role_arn  = module.iam.execution_role_arn
  task_role_arn       = module.iam.task_role_arn
  codedeploy_role_arn = module.iam.codedeploy_role_arn
  alarm_topic_arn     = aws_sns_topic.alerts.arn

  environment = {
    APP_ENV = var.environment
    # Server-side call from the Streamlit container to the API listener.
    BACKEND_URL = "http://${aws_lb.this.dns_name}:${local.ports.api_prod}"
  }
}

module "observability" {
  source = "../modules/observability"

  name_prefix     = local.name_prefix
  environment     = var.environment
  region          = var.region
  alb_arn_suffix  = aws_lb.this.arn_suffix
  alert_topic_arn = aws_sns_topic.alerts.arn

  log_groups = {
    api = module.api.log_group
    ui  = module.ui.log_group
  }

  latency_p99_seconds = var.latency_p99_seconds
}
