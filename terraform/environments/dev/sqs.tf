# Dead-letter queue. The application never reads this queue directly, but
# the worker's application-level retry bound (WORKER_MAX_DELIVERY_ATTEMPTS=4,
# services/worker/src/campaign_worker/config.py) is intentionally lower than
# maxReceiveCount below, per docs/contracts/sqs-message.md.
resource "aws_sqs_queue" "dlq" {
  name                      = "${var.queue_name}-dlq"
  sqs_managed_sse_enabled   = true
  message_retention_seconds = 1209600 # 14 days
}

resource "aws_sqs_queue" "main" {
  name                      = var.queue_name
  sqs_managed_sse_enabled   = true
  message_retention_seconds = 345600 # 4 days

  # Must stay >= worker's SQS_VISIBILITY_TIMEOUT_SECONDS default (180s),
  # services/worker/src/campaign_worker/config.py.
  visibility_timeout_seconds = 180

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 5
  })
}

# Native CloudWatch metric, no extra exporter needed: alerts when messages are sitting
# in the DLQ, i.e. jobs that exhausted every delivery attempt. No alarm_actions/SNS
# topic is wired here deliberately -- that would be unused infrastructure until a
# notification channel is actually chosen; the alarm is still visible/queryable in the
# CloudWatch console today, and adding an action later is a one-line change.
resource "aws_cloudwatch_metric_alarm" "dlq_has_messages" {
  alarm_name          = "${var.queue_name}-dlq-has-messages"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 0
  dimensions = {
    QueueName = aws_sqs_queue.dlq.name
  }
}
