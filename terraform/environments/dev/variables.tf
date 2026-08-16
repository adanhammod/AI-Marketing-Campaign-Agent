variable "aws_region" {
  description = "AWS region for the runtime resources (SQS, DynamoDB, S3, IAM, Bedrock, Polly)."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment name used for tagging and resource naming."
  type        = string
  default     = "dev"
}

variable "queue_name" {
  description = "Name of the main SQS job queue. The DLQ is named \"<queue_name>-dlq\"."
  type        = string
  default     = "campaign-agent-dev-jobs"
}

variable "table_name" {
  description = "Name of the DynamoDB single-table campaign store."
  type        = string
  default     = "campaign-agent-dev"
}

variable "bucket_name" {
  description = "Globally-unique S3 bucket name for campaign artifacts. No default: S3 bucket names must be unique across all of AWS, so this must be supplied explicitly."
  type        = string
}

variable "bedrock_model_id" {
  description = "Bedrock foundation model ID (e.g. \"anthropic.claude-3-haiku-20240307-v1:0\") used for image search-query generation. Used both as the BEDROCK_IMAGE_QUERY_MODEL_ID app env var and to scope the IAM InvokeModel permission."
  type        = string
}

variable "developer_principal_arn" {
  description = "IAM principal ARN (your local AWS identity, from `aws sts get-caller-identity`) allowed to assume the local_runtime role. Never commit the real value — supply via an untracked dev.tfvars."
  type        = string
}

variable "worker_role_name" {
  description = "Optional shared Kubernetes worker IAM role to receive the dev-scoped runtime policy."
  type        = string
  default     = null
  nullable    = true
}
