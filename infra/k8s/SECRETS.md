# Kubernetes secrets

No Secret manifest is committed. Create campaign-secrets separately in each
namespace with SQS_QUEUE_URL, DYNAMODB_TABLE_NAME,
CAMPAIGN_ARTIFACT_BUCKET, BEDROCK_IMAGE_QUERY_MODEL_ID, PEXELS_API_KEY,
CLOUDFLARE_ACCOUNT_ID, and CLOUDFLARE_API_TOKEN. Values should come from a
secret manager or a deployment-time encrypted secret workflow, never Git.

CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN are the active generative image
provider's credentials (Cloudflare Workers AI, FLUX). The API token needs
the "Workers AI Read/Edit" account permission. STABILITY_API_KEY is no
longer required for the worker to run generative mode -- it is kept as an
optional secret only if you intend to roll back to the Stability client,
which remains in the codebase unused (see
`services/worker/src/campaign_worker/providers/stability_client.py`).

AWS credentials are not Kubernetes secrets in this design: pods use the EC2
worker node instance profile. This is acceptable for the single-node kubeadm
MVP, but should be replaced with per-workload pod identity before multi-tenant
use.

`BEDROCK_CREATIVE_PLAN_MODEL_ID`, `POLLY_VOICE_ID`, and `POLLY_ENGINE` are
optional worker config, not secrets — they have safe defaults (deterministic
creative-plan generator, Polly's default voice/`neural` engine) and can be
added to `campaign-config` (not `campaign-secrets`) if you want to override
them; see `services/worker/.env.example` for every variable the worker reads.

## Monitoring: grafana-admin (namespace `monitoring`)

**Required before the `campaign-monitoring` Argo Application is synced.** The
Grafana Deployment (`infra/k8s/monitoring/grafana.yaml`) reads its admin
credentials from a Secret named `grafana-admin` via `secretKeyRef` — without
it, the Grafana pod fails to start (`CrashLoopBackOff`, never `Ready`), since
Kubernetes cannot resolve the referenced key at container-start time. Create
it the same way as `campaign-secrets` — directly against the cluster, never
committed:

```
kubectl create secret generic grafana-admin \
  --namespace monitoring \
  --from-literal=admin-user=admin \
  --from-literal=admin-password=<CHOOSE_A_PASSWORD>
```

## Verifying secrets are SET, without ever printing values

Use `-o jsonpath` to check for key *presence* only — this never decodes or
prints the actual secret value:

```
# campaign-secrets, per namespace (dev/prod)
for key in SQS_QUEUE_URL DYNAMODB_TABLE_NAME CAMPAIGN_ARTIFACT_BUCKET \
           BEDROCK_IMAGE_QUERY_MODEL_ID PEXELS_API_KEY \
           CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_TOKEN; do
  if kubectl get secret campaign-secrets -n dev -o jsonpath="{.data.$key}" 2>/dev/null | grep -q .; then
    echo "$key: SET"
  else
    echo "$key: MISSING"
  fi
done

# grafana-admin, namespace monitoring
for key in admin-user admin-password; do
  if kubectl get secret grafana-admin -n monitoring -o jsonpath="{.data.$key}" 2>/dev/null | grep -q .; then
    echo "$key: SET"
  else
    echo "$key: MISSING"
  fi
done
```

## Docker Hub image pulls

Images now come from Docker Hub instead of ECR (`<DOCKERHUB_USERNAME>/campaign-agent-*`
in `infra/k8s/dev/apps.yaml` and `infra/k8s/prod/apps.yaml`; the CD workflows
rewrite the placeholder to the real namespace on every promotion). No
`imagePullSecrets` are wired into the Deployments yet, because that depends on
a choice not made here: whether the Docker Hub repositories are public or
private.

- **If the repositories are public**: no further action needed. Anonymous
  pulls work and nothing else in this file applies.
- **If the repositories are private**: create a `docker-registry` Secret in
  each namespace (`dev`, `prod`) *before* adding `imagePullSecrets` to the
  Deployments — referencing a Secret that doesn't exist yet will break pulls
  even for images that would otherwise be reachable:

  ```
  kubectl create secret docker-registry dockerhub-pull-secret \
    --namespace dev \
    --docker-server=https://index.docker.io/v1/ \
    --docker-username=<DOCKERHUB_USERNAME> \
    --docker-password=<DOCKERHUB_TOKEN>
  ```

  (repeat for `--namespace prod`). Never commit the token — create the Secret
  directly against the cluster (via SSM session + `kubectl`, as described in
  `terraform/environments/cluster/README.md`) or through a secret-manager /
  sealed-secrets flow if one gets adopted later.

  Then add to each Deployment's pod `spec`:

  ```yaml
  imagePullSecrets:
    - name: dockerhub-pull-secret
  ```

  This one-line addition is intentionally not made yet, since it presumes the
  private-repo choice — add it once the Secret exists and the privacy
  decision is final.
