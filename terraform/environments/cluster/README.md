# Cluster Terraform prerequisites

This root creates the VPC, ALB, kubeadm instances/worker ASG, three ECR
repositories, and the GitHub image-delivery OIDC role. Application DynamoDB,
S3, SQS, and runtime IAM policies remain environment-owned.

External access to the application is: ALB (port 80, public) -> Target Group
-> worker NodePort 30080 -> ingress-nginx (deployed inside the cluster via
GitOps, `infra/k8s/ingress-nginx`) -> Kubernetes Ingress -> frontend/API
ClusterIP Services. The worker NodePort (30080 HTTP, 30443 HTTPS reserved for
future TLS) is reachable only from the ALB security group -- never directly
from the internet; see `terraform/modules/kubeadm-cluster/main.tf`
(`aws_security_group.worker_ingress`), which is attached to every worker via
the launch template so it applies automatically to ASG replacement instances
too. Worker nodes have no Elastic IP, but since the ALB (not the worker's own
public IP) is the entry point, a worker being replaced by the ASG has no
user-visible effect on the public URL.

The ALB's HTTP (port 80) ingress is intentionally hardcoded to `0.0.0.0/0` in
`aws_security_group.alb` (`terraform/modules/kubeadm-cluster/main.tf`) —
**the dev/demo ALB HTTP endpoint is publicly reachable from the internet by
design**, so it never needs an operator CIDR updated when a home IP changes.
This has no effect on SSH, the Kubernetes API, or the worker NodePort, which
stay reachable only from inside the VPC / from the ALB security group.

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
The configured account is 228281126655 and region is us-east-1.

**Registry: ECR is dormant, Docker Hub is active.** `cd-dev.yml`/`cd-prod.yml`
now build and push to Docker Hub (`DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`
secrets), not ECR — `ECR_REGISTRY` is no longer read by any workflow. The
three ECR repositories this root creates (`module.ecr`) are intentionally
**kept, not removed**: they already exist and are imported into the durable
S3 state (`campaign-terraform-state-228281126655`, key
`cluster/terraform.tfstate`), and removing their Terraform configuration now
would plan their destruction. They're retained as legacy/dormant
infrastructure pending the cleanup below.

**Docker Hub migration — future IAM cleanup (do not do yet):** once Docker
Hub image pulls are verified working end-to-end in both dev and prod, these
ECR-only IAM grants become unnecessary and can be removed, in this order —
not before:

1. Confirm Kubernetes successfully pulls `<DOCKERHUB_USERNAME>/campaign-agent-*`
   images in both dev and prod (no `ImagePullBackOff`, full E2E verified).
2. Remove the ECR push policy from `campaign-github-actions`
   (`module.github_oidc.aws_iam_role_policy.ecr` in
   `terraform/modules/github-oidc/main.tf`) — CI no longer pushes to ECR.
3. Remove the `AmazonEC2ContainerRegistryReadOnly` attachment from
   `campaign-cluster-worker` (`aws_iam_role_policy_attachment.worker_ecr` in
   `terraform/modules/kubeadm-cluster/main.tf`) — worker nodes no longer pull
   from ECR.
4. Only after that, optionally decommission the ECR repositories/`module.ecr`
   itself, if they're confirmed to have no remaining purpose (e.g. no
   rollback dependency on previously-pushed ECR images).

Each step should be its own reviewed change, applied only after the step
before it is confirmed safe — not bundled together.

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
