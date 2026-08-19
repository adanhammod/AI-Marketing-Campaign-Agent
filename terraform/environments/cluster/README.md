# Cluster Terraform prerequisites

This root creates the VPC, ALB, kubeadm instances/worker ASG, three ECR
repositories, and the GitHub image-delivery OIDC role. Application DynamoDB,
S3, SQS, and runtime IAM policies remain environment-owned.

Required operator values:

- dev_alb_allowed_cidr: CIDR allowed to reach the private-demo ALB on port 80
  (application traffic only)

Optional operator values:

- music_asset_object_arn: grants CI read access to one approved private S3 MP3

The Kubernetes API (port 6443) is never exposed to any external CIDR. The
`nodes` security group only allows 6443 (and all other inter-node ports)
between members of that same security group, so the control plane and worker
ASG instances can always reach each other regardless of any operator IP.
There is no `k8s_api_allowed_cidr` variable and no dependency on an
operator's public IP for Kubernetes API access.

Cluster administration is done via AWS SSM Session Manager, not direct
`kubectl` from an operator workstation:

```
aws ssm start-session --target <control-plane-instance-id>
# on the instance:
kubectl get nodes
kubectl get pods -A
```

The control-plane instance role already has `AmazonSSMManagedInstanceCore`
attached, so no SSH (port 22) is required or opened. `admin.conf` is written
to `/etc/kubernetes/admin.conf` and copied to `/home/ubuntu/.kube/config`
during bootstrap, so `kubectl` works immediately for the `ubuntu` user (or
root) once connected via SSM — no additional kubeconfig setup is needed.
`<control-plane-instance-id>` is available from the `terraform output
control_plane_id`-equivalent (currently not a root output; use `aws ec2
describe-instances` filtered by the `campaign-cluster-control-plane` tag, or
add a root output if a stable reference is wanted later).

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
