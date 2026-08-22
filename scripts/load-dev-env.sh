#!/usr/bin/env bash
# Source this file (do not execute it) to load local dev configuration for
# services/api and services/worker against the real AWS dev infrastructure:
#
#   source scripts/load-dev-env.sh
#
# Targets real AWS (not LocalStack) -- never sets AWS_ENDPOINT_URL or
# SQS_ENDPOINT_URL. Never echoes PEXELS_API_KEY's value.

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  echo "load-dev-env.sh must be sourced, not executed. Run: source scripts/load-dev-env.sh" >&2
  exit 1
fi

_load_dev_env_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_load_dev_env_repo_root="$(cd "${_load_dev_env_script_dir}/.." && pwd)"
_load_dev_env_tf_dir="${_load_dev_env_repo_root}/terraform/environments/dev"
_load_dev_env_worker_env="${_load_dev_env_repo_root}/services/worker/.env"

if ! command -v terraform >/dev/null 2>&1; then
  echo "load-dev-env.sh: terraform not found on PATH" >&2
  unset _load_dev_env_script_dir _load_dev_env_repo_root _load_dev_env_tf_dir _load_dev_env_worker_env
  return 1
fi

_load_dev_env_tf_output() {
  terraform -chdir="${_load_dev_env_tf_dir}" output -raw "$1" 2>/dev/null
}

_load_dev_env_aws_region="$(_load_dev_env_tf_output aws_region)"
_load_dev_env_sqs_queue_url="$(_load_dev_env_tf_output sqs_queue_url)"
_load_dev_env_dynamodb_table_name="$(_load_dev_env_tf_output dynamodb_table_name)"
_load_dev_env_campaign_artifact_bucket="$(_load_dev_env_tf_output campaign_artifact_bucket)"

if [ -z "${_load_dev_env_aws_region}" ] || [ -z "${_load_dev_env_sqs_queue_url}" ] \
  || [ -z "${_load_dev_env_dynamodb_table_name}" ] || [ -z "${_load_dev_env_campaign_artifact_bucket}" ]; then
  echo "load-dev-env.sh: one or more required Terraform outputs are unavailable." >&2
  echo "  Run: terraform -chdir=${_load_dev_env_tf_dir} output" >&2
  echo "  and confirm aws_region, sqs_queue_url, dynamodb_table_name, campaign_artifact_bucket are all set." >&2
  unset -f _load_dev_env_tf_output
  unset _load_dev_env_script_dir _load_dev_env_repo_root _load_dev_env_tf_dir _load_dev_env_worker_env
  unset _load_dev_env_aws_region _load_dev_env_sqs_queue_url _load_dev_env_dynamodb_table_name _load_dev_env_campaign_artifact_bucket
  return 1
fi

export AWS_REGION="${_load_dev_env_aws_region}"
export SQS_QUEUE_URL="${_load_dev_env_sqs_queue_url}"
export DYNAMODB_TABLE_NAME="${_load_dev_env_dynamodb_table_name}"
export CAMPAIGN_ARTIFACT_BUCKET="${_load_dev_env_campaign_artifact_bucket}"
unset -f _load_dev_env_tf_output
unset _load_dev_env_aws_region _load_dev_env_sqs_queue_url _load_dev_env_dynamodb_table_name _load_dev_env_campaign_artifact_bucket

export AWS_PROFILE=campaign-agent-dev
export REPOSITORY_BACKEND=dynamodb
export QUEUE_BACKEND=sqs
export ENVIRONMENT=local

# Real AWS only for this workflow -- never LocalStack overrides here.
unset AWS_ENDPOINT_URL
unset SQS_ENDPOINT_URL

if [ ! -f "${_load_dev_env_worker_env}" ]; then
  echo "load-dev-env.sh: ${_load_dev_env_worker_env} not found -- cannot load PEXELS_API_KEY / BEDROCK_IMAGE_QUERY_MODEL_ID." >&2
  unset _load_dev_env_script_dir _load_dev_env_repo_root _load_dev_env_tf_dir _load_dev_env_worker_env
  return 1
fi

set -a
# shellcheck disable=SC1090
source "${_load_dev_env_worker_env}"
set +a

if [ -z "${PEXELS_API_KEY:-}" ]; then
  echo "load-dev-env.sh: PEXELS_API_KEY is not set." >&2
  echo "  Add a line to ${_load_dev_env_worker_env}:" >&2
  echo "    PEXELS_API_KEY=<your key>" >&2
  echo "  then re-source this script." >&2
  unset _load_dev_env_script_dir _load_dev_env_repo_root _load_dev_env_tf_dir _load_dev_env_worker_env
  return 1
fi

echo "Loaded dev environment for services/api and services/worker:"
echo "  AWS_PROFILE                 = ${AWS_PROFILE}"
echo "  AWS_REGION                  = ${AWS_REGION}"
echo "  SQS_QUEUE_URL                = ${SQS_QUEUE_URL}"
echo "  DYNAMODB_TABLE_NAME          = ${DYNAMODB_TABLE_NAME}"
echo "  CAMPAIGN_ARTIFACT_BUCKET     = ${CAMPAIGN_ARTIFACT_BUCKET}"
echo "  REPOSITORY_BACKEND           = ${REPOSITORY_BACKEND}"
echo "  QUEUE_BACKEND                 = ${QUEUE_BACKEND}"
echo "  ENVIRONMENT                   = ${ENVIRONMENT}"
echo "  BEDROCK_IMAGE_QUERY_MODEL_ID  = ${BEDROCK_IMAGE_QUERY_MODEL_ID:-<unset>}"
echo "  PEXELS_API_KEY                = PRESENT (value not shown)"

unset _load_dev_env_script_dir _load_dev_env_repo_root _load_dev_env_tf_dir _load_dev_env_worker_env
