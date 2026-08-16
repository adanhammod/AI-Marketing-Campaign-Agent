# Kubernetes secrets

No Secret manifest is committed. Create campaign-secrets separately in each
namespace with SQS_QUEUE_URL, DYNAMODB_TABLE_NAME,
CAMPAIGN_ARTIFACT_BUCKET, BEDROCK_IMAGE_QUERY_MODEL_ID, PEXELS_API_KEY,
and STABILITY_API_KEY. Values should come from a secret manager or a
deployment-time encrypted secret workflow, never Git.

AWS credentials are not Kubernetes secrets in this design: pods use the EC2
worker node instance profile. This is acceptable for the single-node kubeadm
MVP, but should be replaced with per-workload pod identity before multi-tenant
use.
