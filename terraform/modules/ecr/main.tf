variable "repository_names" {
  type = set(string)
}

resource "aws_ecr_repository" "this" {
  for_each             = var.repository_names
  name                 = each.value
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "retain 30 images"
      selection = {
        tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30
      }
      action = {
        type = "expire"
      }

    }]
  })
}

output "repository_urls" {
  value = {
    for k, v in aws_ecr_repository.this : k => v.repository_url
  }
}
output "repository_arns" {
  value = [for v in aws_ecr_repository.this : v.arn]
}
