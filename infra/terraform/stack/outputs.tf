output "ui_url" {
  description = "Open this in a browser."
  value       = "http://${aws_lb.this.dns_name}"
}

output "api_url" {
  value = "http://${aws_lb.this.dns_name}:8080"
}

output "ui_test_url" {
  description = "Blue/green test listener — smoke tested before traffic shifts."
  value       = "http://${aws_lb.this.dns_name}:8081"
}

output "api_test_url" {
  value = "http://${aws_lb.this.dns_name}:8082"
}

output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "services" {
  value = {
    api = {
      ecr_repository   = module.api.ecr_repository_url
      service_name     = module.api.service_name
      task_family      = module.api.task_family
      codedeploy_app   = module.api.codedeploy_app
      codedeploy_group = module.api.codedeploy_group
      log_group        = module.api.log_group
      container_name   = "api"
      container_port   = 8000
    }
    ui = {
      ecr_repository   = module.ui.ecr_repository_url
      service_name     = module.ui.service_name
      task_family      = module.ui.task_family
      codedeploy_app   = module.ui.codedeploy_app
      codedeploy_group = module.ui.codedeploy_group
      log_group        = module.ui.log_group
      container_name   = "ui"
      container_port   = 8501
    }
  }
}

output "secret_arns" {
  value = { for k, s in aws_secretsmanager_secret.app : k => s.arn }
}

output "secret_names" {
  value = { for k, s in aws_secretsmanager_secret.app : k => s.name }
}

output "execution_role_arn" {
  value = module.iam.execution_role_arn
}

output "task_role_arn" {
  value = module.iam.task_role_arn
}

output "github_deploy_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN GitHub repository secret."
  value       = module.iam.github_role_arn
}

output "alert_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "dashboard" {
  value = "https://console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${module.observability.dashboard_name}"
}

output "egress_cidrs" {
  description = "Put this in api_ingress_cidrs for prod once NAT is enabled."
  value       = module.network.egress_cidrs
}
