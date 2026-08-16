provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "ai-marketing-campaign-agent"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

data "aws_partition" "current" {}
