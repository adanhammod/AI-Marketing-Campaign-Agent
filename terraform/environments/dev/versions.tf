terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Local state for this initial runtime-only dev stack. Migrate to an
  # encrypted, versioned S3+DynamoDB remote backend when the fuller
  # M9 Terraform milestone (VPC/EC2/ECR) is built (docs/spec.md §23).
}
