terraform {
  # >= 1.10.0 is required for the S3 backend's native use_lockfile locking below (the
  # same mechanism terraform/environments/cluster already uses) -- avoids needing a
  # separate DynamoDB lock table just for this root.
  required_version = ">= 1.10.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # Previously local-only state for a "production" root -- unlike cluster (which has
  # always had a real S3 backend), this stack's DynamoDB/SQS/S3/IAM state had no
  # remote/shared/locked storage at all. Same bucket as cluster's backend, a distinct
  # key so the two state files never collide.
  backend "s3" {
    bucket       = "campaign-terraform-state-228281126655"
    key          = "prod/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
provider "aws" {
  region = var.aws_region
}
