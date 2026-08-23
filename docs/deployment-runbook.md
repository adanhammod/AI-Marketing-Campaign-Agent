# Deployment runbook

This is the single reference for how a change gets from a developer's laptop to a
running pod, how the application talks to its dependencies, and how to check that any
of it is actually healthy. It does not replace `infra/k8s/README.md` or
`infra/k8s/SECRETS.md` — it ties them together with the CI/CD and Terraform pieces and
gives exact commands.

## 1. Delivery pipeline (GitOps)

```
developer push (dev or main)
        |
        v
GitHub Actions (.github/workflows/cd-dev.yml / cd-prod.yml)
        |  build + push campaign-agent-{frontend,api,worker}:<git-sha> to Docker Hub
        v
Promotion PR (automation/dev-images-<sha> / automation/prod-images-<sha>)
        |  sed-rewrites the image tag in infra/k8s/{dev,prod}/apps.yaml
        |  <-- a human reviews and merges this PR (deliberate GitOps gate, not a gap)
        v
Argo CD -- App-of-Apps (see §1a below)
        |  campaign-agent-dev:  automated (prune+selfHeal) on the dev branch
        |  campaign-agent-prod: MANUAL sync only, on main -- never auto-applied
        v
Kubernetes (kubeadm cluster)
        |  Deployment rollout -> readiness probes -> Service -> Ingress
        v
ingress-nginx (NodePort 30080/30443) <- AWS ALB (dev only; prod has no external
                                          entry point yet, by design -- see
                                          infra/k8s/README.md)
```

Image tags are always the immutable git SHA (`IMAGE_TAG: "${{ github.sha }}"` in both
CD workflows) — the `:dev`/`:main` strings you see in the committed manifests are only
ever transient placeholders between a push and its promotion PR being merged, never
what actually ends up running.

**PR validation** (`.github/workflows/pr-validate.yml`) runs on every pull request:
Terraform `fmt`/`validate` (all four roots), frontend `lint`/`test`/`build`, API
`ruff`/`mypy`/`pytest`, worker `pytest` (see §9 on why worker `ruff`/`mypy` aren't
gated yet), shared `pytest`, and Docker build sanity checks for all three services
(the worker's uses a throwaway placeholder music asset — see the workflow's comment).

## 1a. Argo CD App-of-Apps

Argo CD Applications are themselves Kubernetes objects, so they need to be created
somehow. Before this change, `infra/k8s/argocd/applications.yaml` held all four child
Application definitions, but nothing in the cluster watched *that file* — adding a new
one (like `campaign-monitoring`) still required a manual
`kubectl apply -f infra/k8s/argocd/applications.yaml`. The **App-of-Apps pattern**
fixes this by making one Argo CD Application (the "root") watch the directory that
holds the *other* Applications' definitions, so Argo CD reconciles those definitions
from Git the same way it already reconciles everything else.

```
Argo CD
└── campaign-apps                       (root, infra/k8s/argocd/root-application.yaml)
    ├── campaign-agent-dev               automated (dev branch)
    ├── campaign-agent-prod              MANUAL sync (main branch) -- unchanged
    ├── campaign-ingress-nginx           automated (main branch)
    └── campaign-monitoring              automated (main branch)
```

- **Root Application**: `campaign-apps`, defined in `infra/k8s/argocd/root-application.yaml`.
  Its source path is `infra/k8s/argocd/applications/` — a directory containing *only*
  the four child Application manifests
  (`infra/k8s/argocd/applications/applications.yaml`, unchanged content, just moved).
  `root-application.yaml` itself lives one level up, outside that directory, so
  syncing `campaign-apps` never applies/prunes/touches its own definition — there is
  no self-reference and no recursive sync loop.
- **Sync policy split, and why it's safe**: `campaign-apps` is `automated:
  {prune: true, selfHeal: true}` — a push that edits a child Application's *definition*
  (its `syncPolicy`, `targetRevision`, `path`, or adds/removes a whole Application)
  propagates automatically. That is a different, higher-level thing than *deploying
  workloads*. Each child Application keeps its own, independent `syncPolicy` exactly as
  before: `campaign-agent-dev`, `campaign-ingress-nginx`, and `campaign-monitoring`
  stay automated; **`campaign-agent-prod` stays manual-sync-only**. `campaign-apps`
  auto-updating means "Argo CD always has an up-to-date `campaign-agent-prod` App
  object pointing at the right Git path" — it does **not** mean "Argo CD auto-deploys
  to the `prod` namespace." Someone still has to run `argocd app sync
  campaign-agent-prod` (or click Sync in the UI) for that.
- **One-time bootstrap**: `kubectl apply -f infra/k8s/argocd/root-application.yaml`.
  This is the *only* manual `kubectl apply` step left in the whole flow, and only
  needs to run once per cluster (already wired into
  `.github/workflows/cluster-provision.yml`'s one-time Argo CD bootstrap, so a fresh
  cluster never needs it run by hand at all).
- **After bootstrap**: `git push` (touching anything under
  `infra/k8s/argocd/applications/`) → `campaign-apps` detects the change → the child
  Application objects are created/updated/removed in the cluster → each child syncs
  (or waits for manual sync, for prod) according to its own policy. The old manual
  step (`kubectl apply -f infra/k8s/argocd/applications.yaml`) is gone — don't run it,
  the file no longer exists at that path.

Verify: `kubectl get applications -n argocd` should show `campaign-apps` plus all four
children; `argocd app get campaign-apps` should show `Synced`/`Healthy` with the four
children as its managed resources.

## 2. Application data flow

```
User -> Frontend (React/Vite, served by nginx)
     -> FastAPI (services/api) -- /api/v1/campaigns, /health/live, /health/ready, /metrics
             |
             v
        SQS (campaign-agent-{dev,prod}-jobs)
             |
             v
        Worker (services/worker) -- LangGraph pipeline:
             Brief -> Strategy -> Copy -> Storyboard -> Images -> Voiceover -> Video -> Package -> FINAL
             |
             +-- AWS Bedrock (creative-plan / image-query generation)
             +-- Cloudflare Workers AI FLUX (generative images) -- Pexels fallback
             +-- AWS Polly (voiceover synthesis) -- ffmpeg (loudness/tempo normalization)
             +-- ffmpeg/hyperframes (video rendering)
             |
             v
        DynamoDB (campaign state/events) + S3 (IMAGE/AUDIO/VIDEO/FINAL_PACKAGE artifacts)
```

A message that exhausts all SQS delivery attempts is durably marked `FAILED` on the
campaign (not left `QUEUED`/`error=null`) before it falls into the DLQ — see the
worker failure-handling fix in `services/worker/src/campaign_worker/consumer/sqs_consumer.py`
(`_mark_exhausted_campaign_failed`) and `services/worker/src/campaign_worker/services/job_processor.py`
(`fail_delivery_exhausted`).

## 3. Monitoring data flow

```
API pod (/metrics)  --\
Worker pod (/metrics) --+--> Prometheus (kubernetes_sd: role=pod, prometheus.io/scrape annotations)
kube-state-metrics  --/        |
node-exporter (per node) ------+
kubelet cAdvisor (per node, via API server proxy) -+
                                                    v
                                              Alertmanager (single "default" receiver,
                                              no external notification channel wired
                                              yet -- see infra/k8s/monitoring/alertmanager.yaml)
                                                    ^
                                                    |
                                              Grafana (provisioned datasource + the
                                              "AI Marketing Campaign Agent Overview"
                                              dashboard)
```

Everything under `infra/k8s/monitoring/` is deployed by a single new Argo CD
Application, `campaign-monitoring`, into its own `monitoring` namespace (created by
`infra/k8s/monitoring/namespace.yaml`, part of the same manifest set the Application
manages — not a separate, unrelated namespace object).

**Before syncing `campaign-monitoring`**, create the `grafana-admin` Secret (see
`infra/k8s/SECRETS.md`) — Grafana will not become `Ready` without it.

## 4. Environment separation (dev vs prod)

| | dev | prod |
|---|---|---|
| Namespace | `dev` | `prod` |
| Image tag source | `infra/k8s/dev/apps.yaml`, promoted from `dev` branch pushes | `infra/k8s/prod/apps.yaml`, promoted from `main` branch pushes |
| Argo CD sync | automated (prune + selfHeal) | **manual only** — deliberately, per project policy of never auto-deploying prod |
| External access | ALB -> ingress-nginx NodePort 30080 | none yet (no host/TLS decision made) |
| Replicas / resource sizing | 1/1/1, identical sizing to prod | 1/1/1, identical sizing to dev (no prod-specific scaling has been decided) |
| Terraform state | `terraform/environments/dev` — local state (documented as provisional) | `terraform/environments/prod` — now has a real S3 backend (see §7); DynamoDB has no GSIs (dev has 2), matching prod's simpler query needs so far |
| Secrets | `campaign-secrets` in `dev` namespace | `campaign-secrets` in `prod` namespace — created separately, never shared with dev |

## 5. Secrets

See `infra/k8s/SECRETS.md` for the full list and the SET/MISSING verification
snippet. In short: `campaign-secrets` (per namespace: `dev`, `prod`) and
`grafana-admin` (namespace `monitoring`) must be created directly against the
cluster before their respective Applications are synced — neither is committed to
git, and neither will ever appear there.

## 6. Verification commands

```bash
# Cluster / nodes
kubectl get nodes -o wide
kubectl top nodes

# Workloads (repeat with -n prod once prod is actually synced)
kubectl get pods -n dev -o wide
kubectl get deployments -n dev
kubectl get svc -n dev
kubectl get ingress -n dev
kubectl rollout status deployment/campaign-agent-api -n dev
kubectl rollout status deployment/campaign-agent-worker -n dev
kubectl rollout status deployment/campaign-agent-frontend -n dev
kubectl logs -n dev deployment/campaign-agent-worker --tail=200
kubectl top pods -n dev

# Argo CD -- campaign-apps is the App-of-Apps root; the rest are its children
kubectl get applications -n argocd
argocd app get campaign-apps
argocd app get campaign-agent-dev
argocd app get campaign-agent-prod
argocd app get campaign-ingress-nginx
argocd app get campaign-monitoring

# Monitoring
kubectl get pods -n monitoring
kubectl port-forward -n monitoring svc/prometheus 9090:9090   # then open http://localhost:9090/targets
kubectl port-forward -n monitoring svc/grafana 3000:3000      # then open http://localhost:3000
kubectl port-forward -n monitoring svc/alertmanager 9093:9093 # then open http://localhost:9093

# SQS / DLQ (requires AWS credentials with sqs:GetQueueAttributes)
aws sqs get-queue-attributes --queue-url <SQS_QUEUE_URL> --attribute-names ApproximateNumberOfMessages
aws sqs get-queue-attributes --queue-url <SQS_DLQ_URL> --attribute-names ApproximateNumberOfMessagesVisible
```

## 7. Terraform

Terraform changes in this pass are **file edits only** — nothing has been applied.
Before applying:

- `terraform/environments/prod` now has a real S3 backend (`prod/terraform.tfstate`,
  same bucket as `cluster`). The very first `terraform init` after this change will
  need to migrate any existing local state: `terraform -chdir=terraform/environments/prod init -migrate-state`.
- `terraform/environments/prod` now requires a `bedrock_model_id` variable (previously
  the Bedrock IAM statement was unscoped `Resource: "*"`) — supply it via a `.tfvars`
  file before planning/applying, the same way `terraform/environments/dev/dev.tfvars.example`
  documents it for dev.
- `terraform/environments/cluster`'s `control_plane_ssh_allowed_cidr` variable now
  defaults to `null` (no SSH security group at all) instead of the previous hardcoded
  `0.0.0.0/0`. If you need SSH access to the control plane, set this explicitly to your
  own admin CIDR via `.tfvars` — never commit it, never set it to `0.0.0.0/0`.

## 8. Alerting follow-up

Alertmanager currently has a single `default` receiver with no external integration —
alerts are visible in its own UI/API but nothing gets paged. Similarly, the new SQS
DLQ CloudWatch alarms (`dlq_has_messages`, dev and prod) have no `alarm_actions`/SNS
subscriber. Both are deliberate: wiring a real notification channel (Slack webhook,
PagerDuty, SNS + email) is a one-line follow-up once you decide where alerts should
actually go, rather than standing up unused infrastructure now.
