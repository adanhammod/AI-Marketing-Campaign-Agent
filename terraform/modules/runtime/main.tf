variable "name" {
  type = string
}
variable "artifact_bucket_name" {
  type = string
}

resource "aws_dynamodb_table" "campaigns" {
  name         = var.name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"
  attribute {
    name = "PK"
    type = "S"
  }
  attribute {
    name = "SK"
    type = "S"
  }
  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled = true
  }
}

resource "aws_sqs_queue" "dlq" {
  name = "${var.name}-jobs-dlq"
}
resource "aws_sqs_queue" "jobs" {
  name                       = "${var.name}-jobs"
  visibility_timeout_seconds = 1200
  receive_wait_time_seconds  = 20
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 4

  })
}

resource "aws_s3_bucket" "artifacts" {
  bucket = var.artifact_bucket_name
}
resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"

    }

  }
}

output "table_name" {
  value = aws_dynamodb_table.campaigns.name
}
output "table_arn" {
  value = aws_dynamodb_table.campaigns.arn
}
output "queue_url" {
  value = aws_sqs_queue.jobs.url
}
output "queue_arn" {
  value = aws_sqs_queue.jobs.arn
}
output "bucket_name" {
  value = aws_s3_bucket.artifacts.id
}
output "bucket_arn" {
  value = aws_s3_bucket.artifacts.arn
}
