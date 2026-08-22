# Least-privilege role assumable only by the developer's own AWS identity —
# local FastAPI/worker processes pick this up via the default boto3
# credential chain (an assumed-role profile in ~/.aws/config), never via
# static access keys (docs/spec.md §22: "static AWS keys are forbidden").
locals {
  # On-demand foundation-model ARN. If var.bedrock_model_id refers to a
  # cross-region inference profile instead of a base foundation model, this
  # ARN shape is wrong and must be replaced with the inference-profile ARN
  # format (arn:...:bedrock:<region>:<account-id>:inference-profile/<id>).
  bedrock_model_arn = "arn:${data.aws_partition.current.partition}:bedrock:${var.aws_region}::foundation-model/${var.bedrock_model_id}"
}

data "aws_iam_policy_document" "assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [var.developer_principal_arn]
    }
  }
}

resource "aws_iam_role" "local_runtime" {
  name               = "campaign-agent-${var.environment}-local-runtime"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

data "aws_iam_policy_document" "local_runtime" {
  statement {
    sid    = "Sqs"
    effect = "Allow"
    actions = [
      "sqs:SendMessage",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.main.arn]
  }

  statement {
    sid    = "DynamoDb"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
      "dynamodb:TransactWriteItems",
      "dynamodb:TransactGetItems",
      "dynamodb:ConditionCheckItem",
      "dynamodb:DescribeTable",
    ]
    resources = [
      aws_dynamodb_table.campaigns.arn,
      "${aws_dynamodb_table.campaigns.arn}/index/*",
    ]
  }

  statement {
    sid    = "S3"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.artifacts.arn}/campaigns/*"]
  }

  statement {
    sid       = "S3ListForReconciliation"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.artifacts.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["campaigns/*"]
    }
  }

  statement {
    sid       = "BedrockInvoke"
    effect    = "Allow"
    actions   = ["bedrock:InvokeModel"]
    resources = [local.bedrock_model_arn]
  }

  statement {
    sid    = "PollySynthesize"
    effect = "Allow"
    # Polly has no resource-level ARNs for SynthesizeSpeech; "*" is required
    # by the action itself, not a scoping shortcut.
    actions   = ["polly:SynthesizeSpeech"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "local_runtime" {
  name   = "campaign-agent-${var.environment}-local-runtime"
  policy = data.aws_iam_policy_document.local_runtime.json
}

resource "aws_iam_role_policy_attachment" "local_runtime" {
  role       = aws_iam_role.local_runtime.name
  policy_arn = aws_iam_policy.local_runtime.arn
}

locals {
  # Explicit var.worker_role_name always wins; otherwise fall back to the
  # cluster environment's real output. try() keeps this safe if the cluster
  # stack hasn't been applied yet.
  worker_role_name = try(
    coalesce(
      var.worker_role_name,
      try(data.terraform_remote_state.cluster.outputs.worker_role_name, null),
    ),
    null,
  )
}

resource "aws_iam_role_policy_attachment" "worker_runtime" {
  count = try(trimspace(local.worker_role_name), "") == "" ? 0 : 1

  role       = local.worker_role_name
  policy_arn = aws_iam_policy.local_runtime.arn
}
