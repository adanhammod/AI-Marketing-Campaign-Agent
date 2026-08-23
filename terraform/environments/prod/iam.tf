# Scoped the same way terraform/environments/dev/iam.tf already scopes its
# BedrockInvoke statement -- previously this policy used Resource = "*" for Bedrock,
# broader than dev's ARN-scoped equivalent for no reason tied to prod's actual needs.
variable "bedrock_model_id" {
  description = "Bedrock foundation model ID the worker is allowed to invoke (image-query / creative-plan generation). If it refers to a cross-region inference profile instead of a base foundation model, bedrock_model_arn below must be replaced with the inference-profile ARN format."
  type        = string
}

data "aws_partition" "current" {}

locals {
  bedrock_model_arn = "arn:${data.aws_partition.current.partition}:bedrock:${var.aws_region}::foundation-model/${var.bedrock_model_id}"
}

resource "aws_iam_policy" "runtime" {
  name = "campaign-agent-prod-runtime"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DynamoDb"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query", "dynamodb:TransactWriteItems", "dynamodb:TransactGetItems", "dynamodb:ConditionCheckItem", "dynamodb:DescribeTable"]
        Resource = [module.runtime.table_arn, "${module.runtime.table_arn}/index/*"]
      },
      {
        Sid      = "Sqs"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"]
        Resource = module.runtime.queue_arn
      },
      {
        Sid      = "S3Objects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${module.runtime.bucket_arn}/campaigns/*"
      },
      {
        Sid      = "S3ListForReconciliation"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = module.runtime.bucket_arn
        Condition = {
          StringLike = {
            "s3:prefix" = ["campaigns/*"]
          }
        }
      },
      {
        Sid      = "BedrockInvoke"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = local.bedrock_model_arn
      },
      {
        Sid      = "PollySynthesize"
        Effect   = "Allow"
        Action   = ["polly:SynthesizeSpeech"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "worker_runtime" {
  count = try(trimspace(local.worker_role_name), "") == "" ? 0 : 1

  role       = local.worker_role_name
  policy_arn = aws_iam_policy.runtime.arn
}
