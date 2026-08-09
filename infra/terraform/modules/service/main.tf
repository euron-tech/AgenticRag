# One containerised service: registry, logs, task definition, blue/green
# target groups, listeners, ECS service, and its CodeDeploy application.
#
# Alarms live here rather than in the observability module because the
# CodeDeploy deployment group references them as rollback triggers, and putting
# them elsewhere would create a dependency cycle between the two modules.

resource "aws_ecr_repository" "this" {
  name                 = "${var.name_prefix}-${var.name}"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # so `terraform destroy` does not stall on stored images

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 10 images; untagged sprawl is a silent cost"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name_prefix}/${var.name}"
  retention_in_days = var.log_retention_days
}

# ----------------------------------------------------------- security group
resource "aws_security_group" "task" {
  name        = "${var.name_prefix}-${var.name}-task"
  description = "ECS tasks for ${var.name}"
  vpc_id      = var.vpc_id

  ingress {
    description     = "From the load balancer only"
    from_port       = var.container_port
    to_port         = var.container_port
    protocol        = "tcp"
    security_groups = [var.alb_security_group_id]
  }

  egress {
    description = "Outbound to Supabase, OpenAI, ECR and CloudWatch"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name_prefix}-${var.name}-task" }
}

# ------------------------------------------------------------ target groups
# Two identical groups. CodeDeploy points the listener at whichever one holds
# the new version, then drains the other.
resource "aws_lb_target_group" "blue" {
  name        = substr("${var.name_prefix}-${var.name}-b", 0, 32)
  port        = var.container_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  deregistration_delay = 30

  health_check {
    path                = var.health_check_path
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
    matcher             = "200"
  }
}

resource "aws_lb_target_group" "green" {
  name        = substr("${var.name_prefix}-${var.name}-g", 0, 32)
  port        = var.container_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  deregistration_delay = 30

  health_check {
    path                = var.health_check_path
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
    matcher             = "200"
  }
}

# ---------------------------------------------------------------- listeners
# Production traffic, and a separate test listener the smoke test hits before
# any real traffic shifts.
resource "aws_lb_listener" "prod" {
  load_balancer_arn = var.alb_arn
  port              = var.prod_port
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.blue.arn
  }

  # CodeDeploy owns which target group this points at after the first deploy.
  lifecycle {
    ignore_changes = [default_action]
  }
}

resource "aws_lb_listener" "test" {
  load_balancer_arn = var.alb_arn
  port              = var.test_port
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.green.arn
  }

  lifecycle {
    ignore_changes = [default_action]
  }
}

# --------------------------------------------------------- task definition
resource "aws_ecs_task_definition" "this" {
  family                   = "${var.name_prefix}-${var.name}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([{
    name      = var.name
    image     = var.image
    essential = true

    portMappings = [{
      containerPort = var.container_port
      protocol      = "tcp"
    }]

    environment = [for k, v in var.environment : { name = k, value = tostring(v) }]

    # Injected by the ECS agent at start. Secrets are never baked into an image
    # and never appear as plaintext in the task definition.
    secrets = [for k, v in var.secrets : { name = k, valueFrom = v }]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.this.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = var.name
      }
    }
  }])
}

# ------------------------------------------------------------- ecs service
resource "aws_ecs_service" "this" {
  name            = "${var.name_prefix}-${var.name}"
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  deployment_controller {
    type = "CODE_DEPLOY"
  }

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = var.assign_public_ip
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.blue.arn
    container_name   = var.name
    container_port   = var.container_port
  }

  health_check_grace_period_seconds = 90

  # After the first apply, CodeDeploy owns the task definition and which target
  # group is live. Terraform must not fight it.
  lifecycle {
    ignore_changes = [task_definition, load_balancer, desired_count]
  }

  depends_on = [aws_lb_listener.prod, aws_lb_listener.test]
}

# ------------------------------------------------------------------ alarms
resource "aws_cloudwatch_metric_alarm" "target_5xx" {
  for_each = {
    blue  = aws_lb_target_group.blue.arn_suffix
    green = aws_lb_target_group.green.arn_suffix
  }

  alarm_name          = "${var.name_prefix}-${var.name}-${each.key}-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = var.error_alarm_threshold
  treat_missing_data  = "notBreaching"
  alarm_description   = "5xx responses from ${var.name} (${each.key}). Wired to CodeDeploy rollback."

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = each.value
  }

  alarm_actions = var.alarm_topic_arn == "" ? [] : [var.alarm_topic_arn]
  ok_actions    = var.alarm_topic_arn == "" ? [] : [var.alarm_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_hosts" {
  for_each = {
    blue  = aws_lb_target_group.blue.arn_suffix
    green = aws_lb_target_group.green.arn_suffix
  }

  alarm_name          = "${var.name_prefix}-${var.name}-${each.key}-unhealthy"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_description   = "Unhealthy ${var.name} tasks (${each.key})."

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = each.value
  }

  alarm_actions = var.alarm_topic_arn == "" ? [] : [var.alarm_topic_arn]
}

# -------------------------------------------------------------- codedeploy
resource "aws_codedeploy_app" "this" {
  name             = "${var.name_prefix}-${var.name}"
  compute_platform = "ECS"
}

resource "aws_codedeploy_deployment_group" "this" {
  app_name               = aws_codedeploy_app.this.name
  deployment_group_name  = "${var.name_prefix}-${var.name}"
  service_role_arn       = var.codedeploy_role_arn
  deployment_config_name = var.deployment_config

  deployment_style {
    deployment_option = "WITH_TRAFFIC_CONTROL"
    deployment_type   = "BLUE_GREEN"
  }

  blue_green_deployment_config {
    # STOP_DEPLOYMENT holds the deployment at "Ready" once the replacement task
    # set is healthy but before any production traffic moves. manage.py smoke
    # tests the test listener at that point and only then calls
    # `aws deploy continue-deployment`. Without this gate, "blue/green" is just
    # a rolling restart with extra steps.
    deployment_ready_option {
      action_on_timeout    = var.wait_for_approval ? "STOP_DEPLOYMENT" : "CONTINUE_DEPLOYMENT"
      wait_time_in_minutes = var.wait_for_approval ? var.approval_wait_minutes : 0
    }

    terminate_blue_instances_on_deployment_success {
      action                           = "TERMINATE"
      termination_wait_time_in_minutes = var.bake_minutes
    }
  }

  # A deploy that trips an alarm rolls itself back. Rollback is not a manual step.
  auto_rollback_configuration {
    enabled = true
    events  = ["DEPLOYMENT_FAILURE", "DEPLOYMENT_STOP_ON_ALARM"]
  }

  alarm_configuration {
    enabled = true
    alarms = concat(
      [for a in aws_cloudwatch_metric_alarm.target_5xx : a.alarm_name],
      [for a in aws_cloudwatch_metric_alarm.unhealthy_hosts : a.alarm_name],
    )
  }

  ecs_service {
    cluster_name = var.cluster_name
    service_name = aws_ecs_service.this.name
  }

  load_balancer_info {
    target_group_pair_info {
      prod_traffic_route {
        listener_arns = [aws_lb_listener.prod.arn]
      }

      test_traffic_route {
        listener_arns = [aws_lb_listener.test.arn]
      }

      target_group {
        name = aws_lb_target_group.blue.name
      }

      target_group {
        name = aws_lb_target_group.green.name
      }
    }
  }
}
