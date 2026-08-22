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
