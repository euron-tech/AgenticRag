output "vpc_id" {
  value = aws_vpc.this.id
}

output "vpc_cidr" {
  value = aws_vpc.this.cidr_block
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "task_subnet_ids" {
  description = "Where ECS tasks run: private when NAT is enabled, public otherwise."
  value       = var.enable_nat ? aws_subnet.private[*].id : aws_subnet.public[*].id
}

output "assign_public_ip" {
  description = "Tasks without a NAT gateway need a public IP to reach Supabase and OpenAI."
  value       = !var.enable_nat
}

output "egress_cidrs" {
  description = "Source addresses the tasks appear as when calling back into the ALB."
  value       = var.enable_nat ? ["${aws_eip.nat[0].public_ip}/32"] : ["0.0.0.0/0"]
}
