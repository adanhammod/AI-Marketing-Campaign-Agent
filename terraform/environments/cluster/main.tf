variable "aws_region" {
  type    = string
  default = "us-east-1"
}
variable "music_asset_object_arn" {
  description = "Optional ARN of the private S3 music object restored by CI."
  type        = string
  default     = null
  nullable    = true
}
variable "control_plane_ssh_allowed_cidr" {
  description = "CIDR allowed to SSH (TCP/22) to the control plane. Never 0.0.0.0/0 -- set this to your own admin IP/CIDR (e.g. \"203.0.113.4/32\") via a .tfvars file, never committed. Leave unset (null) to disable the SSH security group entirely and manage the control plane via SSM only."
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
  source                 = "../../modules/kubeadm-cluster"
  name                   = "campaign-cluster"
  vpc_id                 = module.network.vpc_id
  subnet_ids             = module.network.public_subnet_ids
  aws_region             = var.aws_region
  control_plane_key_name = "adan-key"
  # Was hardcoded to "0.0.0.0/0" (SSH open to the world) here, directly contradicting
  # this same module's own "Never 0.0.0.0/0" contract on the variable below -- now
  # passed through from this root's own variable instead, which defaults to null (no
  # SSH security group at all) rather than any public CIDR.
  control_plane_ssh_allowed_cidr = var.control_plane_ssh_allowed_cidr
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
output "control_plane_id" {
  value = module.cluster.control_plane_id
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
