# Cluster Terraform prerequisites

This root creates the VPC, ALB, kubeadm instances/worker ASG, three ECR
repositories, and the GitHub image-delivery OIDC role. Application DynamoDB,
S3, SQS, and runtime IAM policies remain environment-owned.

Required operator values:

- k8s_api_allowed_cidr

Optional operator values:

- dev_alb_allowed_cidr: defaults to k8s_api_allowed_cidr for the private demo
- music_asset_object_arn: grants CI read access to one approved private S3 MP3

When music is enabled, set CINEMATIC_MUSIC_ARTIFACT_URI to the matching s3://
URI. No music S3 permission is created when the object ARN is null or empty.
Set ECR_REGISTRY from the registry host portion of the
ecr_repository_urls Terraform output. The configured account is 228281126655
and region is us-east-1; Kubernetes manifests contain the resulting complete
repository URLs, not an account placeholder.

AWS_TERRAFORM_ROLE_ARN is intentionally an externally bootstrapped role because
the cluster workflow cannot assume a role that the same not-yet-run cluster
root is responsible for creating. The role trust must be limited to
repo:adanhammod/AI-Marketing-Campaign-Agent:ref:refs/heads/main and the
sts.amazonaws.com audience. Its policy should be reviewed and limited to the
resources managed by this root. Create and review that bootstrap role before
enabling cluster-provision.yml; its creation is not performed in this session.

The control plane stores a generated join command in the Terraform-created
SecureString parameter and refreshes the 12-hour token every six hours. Worker
ASG nodes retry retrieval, validate the command prefix, and skip kubeadm join
when /etc/kubernetes/kubelet.conf already exists.
