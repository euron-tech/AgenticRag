output "ecr_repository_url" {
  value = aws_ecr_repository.this.repository_url
}

output "ecr_repository_name" {
  value = aws_ecr_repository.this.name
}

output "service_name" {
  value = aws_ecs_service.this.name
}

output "task_family" {
  value = aws_ecs_task_definition.this.family
}

output "codedeploy_app" {
  value = aws_codedeploy_app.this.name
}

output "codedeploy_group" {
  value = aws_codedeploy_deployment_group.this.deployment_group_name
}

output "log_group" {
  value = aws_cloudwatch_log_group.this.name
}

output "blue_target_group" {
  value = aws_lb_target_group.blue.name
}

output "green_target_group" {
  value = aws_lb_target_group.green.name
}

output "target_group_arn_suffixes" {
  value = {
    blue  = aws_lb_target_group.blue.arn_suffix
    green = aws_lb_target_group.green.arn_suffix
  }
}

output "task_security_group_id" {
  value = aws_security_group.task.id
}
