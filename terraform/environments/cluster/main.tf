variable "aws_region" {
  type    = string
  default = "us-east-1"
}
variable "dev_alb_allowed_cidr" {
  description = "CIDR allowed to reach the private-demo ALB (port 80, application traffic only). The Kubernetes API is never exposed to this or any external CIDR; it is cluster-internal only, administered via AWS SSM Session Manager."
  type        = string
}
variable "music_asset_object_arn" {
  description = "Optional ARN of the private S3 music object restored by CI."
  type        = string
  default     = null
  nullable    = true
}

module "network" {
  source = "../../modules/network"
  name   = "campaign-cluster"
}
module "ecr" {
  source           = "../../modules/ecr"
  repository_names = ["campaign-agent-frontend", "campaign-agent-api", "campaign-agent-worker"]
}
module "cluster" {
  source                         = "../../modules/kubeadm-cluster"
  name                           = "campaign-cluster"
  vpc_id                         = module.network.vpc_id
  subnet_ids                     = module.network.public_subnet_ids
  dev_alb_allowed_cidr           = var.dev_alb_allowed_cidr
  aws_region                     = var.aws_region
  control_plane_key_name         = "adan-key"
  control_plane_ssh_allowed_cidr = "147.235.217.230/32"
}
module "github_oidc" {
  source                 = "../../modules/github-oidc"
  repository             = "adanhammod/AI-Marketing-Campaign-Agent"
  owner_id               = "157805409"
  repository_id          = "1323726036"
  ecr_repository_arns    = module.ecr.repository_arns
  music_asset_object_arn = var.music_asset_object_arn
}
output "alb_dns_name" {
  value = module.cluster.alb_dns_name
}
output "ecr_repository_urls" {
  value = module.ecr.repository_urls
}
output "github_actions_role_arn" {
  value = module.github_oidc.role_arn
}
output "worker_role_name" {
  value = module.cluster.worker_role_name
}
output "worker_role_arn" {
  value = module.cluster.worker_role_arn
}
