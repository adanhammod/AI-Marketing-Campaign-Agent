# ingress-nginx

`controller.yaml` is the upstream `ingress-nginx` bare-metal install manifest,
vendored unmodified except for the `ingress-nginx-controller` Service:

- Source: `https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.13.0/deploy/static/provider/baremetal/deploy.yaml`
- Patch: added `nodePort: 30080` to the `http` port and `nodePort: 30443` to
  the `https` port, so the Service's NodePort assignment is fixed instead of
  random. These two ports must stay in sync with the worker node security
  group rules in `terraform/modules/kubeadm-cluster/main.tf`
  (`aws_security_group.worker_ingress`).

Deployed via the `campaign-ingress-nginx` Argo CD Application
(`infra/k8s/argocd/applications.yaml`), synced from the `main` branch to the
`ingress-nginx` namespace on every cluster, ahead of the per-environment
`campaign-agent-dev` / `campaign-agent-prod` Applications.

To pick up a newer ingress-nginx release, re-fetch the upstream manifest and
reapply the same two-line NodePort patch — do not hand-edit anything else in
this file.
