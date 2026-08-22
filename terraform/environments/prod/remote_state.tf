# Reads the shared kubeadm worker IAM role name directly from the cluster
# environment's own Terraform state, so it never has to be hand-copied into
# a prod tfvars file. var.worker_role_name still wins if set explicitly;
# this is only the automatic fallback.
data "terraform_remote_state" "cluster" {
  backend = "s3"
  config = {
    bucket = "campaign-terraform-state-228281126655"
    key    = "cluster/terraform.tfstate"
    region = "us-east-1"
  }
}

locals {
  worker_role_name = try(
    coalesce(
      var.worker_role_name,
      try(data.terraform_remote_state.cluster.outputs.worker_role_name, null),
    ),
    null,
  )
}
