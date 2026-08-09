output "execution_role_arn" {
  value = aws_iam_role.execution.arn
}

output "task_role_arn" {
  value = aws_iam_role.task.arn
}

output "codedeploy_role_arn" {
  value = aws_iam_role.codedeploy.arn
}

output "github_role_arn" {
  description = "Set this as AWS_DEPLOY_ROLE_ARN in the GitHub repository secrets."
  value       = var.create_github_role ? aws_iam_role.github[0].arn : null
}
