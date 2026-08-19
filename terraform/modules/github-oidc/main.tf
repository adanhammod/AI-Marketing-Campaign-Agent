variable "repository" {
  type = string
}
variable "ecr_repository_arns" {
  type = list(string)
}
variable "music_asset_object_arn" {
  description = "Optional S3 object ARN that CI may download for the image build."
  type        = string
  default     = null
  nullable    = true
}

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}
resource "aws_iam_role" "github" {
  name = "campaign-github-actions"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRoleWithWebIdentity"
      Principal = {
        Federated = data.aws_iam_openid_connect_provider.github.arn
      }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = [
            "repo:${var.repository}:ref:refs/heads/dev",
            "repo:${var.repository}:ref:refs/heads/main",
          ]
        }

      }

    }]

  })
}
resource "aws_iam_role_policy" "ecr" {
  role = aws_iam_role.github.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*"
      },
      {
        Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage", "ecr:PutImage", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload"], Resource = var.ecr_repository_arns
      }
      ], try(trimspace(var.music_asset_object_arn), "") == "" ? [] : [
      {
        Effect = "Allow", Action = ["s3:GetObject"], Resource = var.music_asset_object_arn
      }
    ])

  })
}
output "role_arn" {
  value = aws_iam_role.github.arn
}
