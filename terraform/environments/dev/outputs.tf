output "aws_region" {
  description = "AWS region runtime resources are deployed in."
  value       = var.aws_region
}

output "sqs_queue_url" {
  description = "URL of the main SQS job queue (SQS_QUEUE_URL for services/api and services/worker)."
  value       = aws_sqs_queue.main.id
}

output "sqs_dlq_url" {
  description = "URL of the SQS dead-letter queue (ops visibility only; not read by app code)."
  value       = aws_sqs_queue.dlq.id
}

output "dynamodb_table_name" {
  description = "Name of the DynamoDB campaign table (DYNAMODB_TABLE_NAME)."
  value       = aws_dynamodb_table.campaigns.name
}

output "campaign_artifact_bucket" {
  description = "Name of the S3 bucket for campaign artifacts (CAMPAIGN_ARTIFACT_BUCKET)."
  value       = aws_s3_bucket.artifacts.bucket
}

output "iam_role_arn_to_assume" {
  description = "ARN of the IAM role local FastAPI/worker processes should assume via an AWS CLI profile."
  value       = aws_iam_role.local_runtime.arn
}
