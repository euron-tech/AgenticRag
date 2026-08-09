# Alerting destination, application-level alarms, and the environment dashboard.
#
# Per-service ALB alarms live in the service module because CodeDeploy
# references them for rollback. What is here is everything else: the topic,
# alarms derived from application metrics and logs, and the dashboard.

# The topic is created by the stack, not here: the service module also needs it
# for its rollback alarms, and creating it in both places would be a cycle.

# ------------------------------------------------- application log metrics
resource "aws_cloudwatch_log_metric_filter" "errors" {
  for_each = var.log_groups

  name           = "${var.name_prefix}-${each.key}-errors"
  log_group_name = each.value
  pattern        = "{ $.level = \"ERROR\" }"

  metric_transformation {
    name          = "ApplicationErrors"
    namespace     = "AgenticRAG/${var.environment}"
    value         = "1"
    default_value = "0"
    dimensions    = {}
  }
}

resource "aws_cloudwatch_metric_alarm" "application_errors" {
  alarm_name          = "${var.name_prefix}-application-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ApplicationErrors"
  namespace           = "AgenticRAG/${var.environment}"
  period              = 300
  statistic           = "Sum"
  threshold           = var.error_log_threshold
  treat_missing_data  = "notBreaching"
  alarm_description   = "Sustained ERROR-level log output."
  alarm_actions       = [var.alert_topic_arn]
  ok_actions          = [var.alert_topic_arn]

  depends_on = [aws_cloudwatch_log_metric_filter.errors]
}

resource "aws_cloudwatch_metric_alarm" "ingestion_failures" {
  alarm_name          = "${var.name_prefix}-ingestion-failures"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "IngestionFailures"
  namespace           = "AgenticRAG"
  period              = 900
  statistic           = "Sum"
  threshold           = var.ingestion_failure_threshold
  treat_missing_data  = "notBreaching"
  alarm_description   = "Documents are failing to process. Check the admin console for reasons."
  alarm_actions       = [var.alert_topic_arn]

  dimensions = {
    Service     = "agentic-rag-api"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_metric_alarm" "api_latency" {
  alarm_name          = "${var.name_prefix}-api-latency-p99"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  extended_statistic  = "p99"
  threshold           = var.latency_p99_seconds
  treat_missing_data  = "notBreaching"
  alarm_description   = "p99 response time is above the agreed ceiling."
  alarm_actions       = [var.alert_topic_arn]

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
  }
}

# --------------------------------------------------------------- dashboard
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.name_prefix}-overview"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title  = "Requests and errors"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", var.alb_arn_suffix, { stat = "Sum" }],
            [".", "HTTPCode_ELB_5XX_Count", ".", ".", { stat = "Sum" }],
            [".", "HTTPCode_Target_5XX_Count", ".", ".", { stat = "Sum" }]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title  = "Response time"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", var.alb_arn_suffix, { stat = "p50" }],
            ["...", { stat = "p99" }]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title  = "Chat turn latency and answer quality"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AgenticRAG", "ChatTurnDuration", { stat = "Average" }],
            [".", "RetrievalDuration", { stat = "Average" }],
            [".", "NoAnswer", { stat = "Sum" }],
            [".", "UngroundedDrafts", { stat = "Sum" }]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title  = "Ingestion"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AgenticRAG", "DocumentsIndexed", { stat = "Sum" }],
            [".", "ChunksIndexed", { stat = "Sum" }],
            [".", "IngestionFailures", { stat = "Sum" }],
            [".", "IngestionDuration", { stat = "Average" }]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 12, height = 6
        properties = {
          title  = "Token spend"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AgenticRAG", "ChatTokens", { stat = "Sum" }],
            [".", "EmbeddingTokens", { stat = "Sum" }]
          ]
        }
      },
      {
        type = "log", x = 12, y = 12, width = 12, height = 6
        properties = {
          title  = "Recent errors"
          region = var.region
          query  = "SOURCE '${try(values(var.log_groups)[0], "")}' | fields @timestamp, message, request_id | filter level = 'ERROR' | sort @timestamp desc | limit 20"
          view   = "table"
        }
      }
    ]
  })
}
