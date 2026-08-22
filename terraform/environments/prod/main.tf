variable "aws_region" {
  type    = string
  default = "us-east-1"
}
variable "artifact_bucket_name" {
  type = string
}
variable "worker_role_name" {
  description = "Shared Kubernetes worker IAM role created by the cluster environment. Optional override — leave unset to derive it automatically from the cluster environment's Terraform state."
  type        = string
  default     = null
  nullable    = true
}
module "runtime" {
  source               = "../../modules/runtime"
  name                 = "campaign-agent-prod"
  artifact_bucket_name = var.artifact_bucket_name
}
output "table_name" {
  value = module.runtime.table_name
}
output "queue_url" {
  value = module.runtime.queue_url
}
output "bucket_name" {
  value = module.runtime.bucket_name
}
output "runtime_policy_arn" {
  value = aws_iam_policy.runtime.arn
}
