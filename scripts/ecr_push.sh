#!/usr/bin/env bash
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
QUERY_REPO="${QUERY_REPO_NAME:-cina-query}"
INGEST_REPO="${INGEST_REPO_NAME:-cina-ingestion}"
TAG="${IMAGE_TAG:-latest}"

ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
QUERY_IMAGE="${ECR_REGISTRY}/${QUERY_REPO}:${TAG}"
INGEST_IMAGE="${ECR_REGISTRY}/${INGEST_REPO}:${TAG}"

aws ecr get-login-password --region "${REGION}" | docker login --username AWS --password-stdin "${ECR_REGISTRY}"

aws ecr describe-repositories --region "${REGION}" --repository-names "${QUERY_REPO}" >/dev/null 2>&1 || \
  aws ecr create-repository --region "${REGION}" --repository-name "${QUERY_REPO}" >/dev/null

aws ecr describe-repositories --region "${REGION}" --repository-names "${INGEST_REPO}" >/dev/null 2>&1 || \
  aws ecr create-repository --region "${REGION}" --repository-name "${INGEST_REPO}" >/dev/null

docker build -f Dockerfile.query -t "${QUERY_IMAGE}" .
docker build -f Dockerfile.ingestion -t "${INGEST_IMAGE}" .

docker push "${QUERY_IMAGE}"
docker push "${INGEST_IMAGE}"

echo "QUERY_IMAGE=${QUERY_IMAGE}"
echo "INGEST_IMAGE=${INGEST_IMAGE}"
