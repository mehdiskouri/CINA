# Phase 4 AWS Deployment Runbook

## 1. Prerequisites

- AWS CLI configured with a user/role that can manage VPC, ECS, RDS, ElastiCache, SQS, S3, IAM, and ECR.
- Docker installed and authenticated to build and push images.
- Terraform >= 1.6 installed.

## 2. Prepare Variables

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
```

Fill `terraform.tfvars` with real values:

- `db_password`
- `document_bucket_name` (must be globally unique)
- `anthropic_api_key`
- `openai_api_key`

## 3. Build and Push Images

From repo root:

```bash
export AWS_REGION=us-east-1
./scripts/ecr_push.sh
```

If needed, override repository names and tag:

```bash
QUERY_REPO_NAME=cina-query INGEST_REPO_NAME=cina-ingestion IMAGE_TAG=v1 ./scripts/ecr_push.sh
```

## 4. Terraform Plan and Apply

```bash
cd infra/terraform
terraform init
terraform fmt -recursive
terraform validate
terraform plan -out=tfplan
terraform apply tfplan
```

Capture and store outputs:

```bash
terraform output
```

## 5. Post-Apply Setup

1. Create DB extensions on RDS:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

2. Run migrations against RDS:

```bash
export DATABASE_URL="postgresql://<user>:<pass>@<rds-endpoint>:5432/<db>"
python -m cina db migrate
```

3. Set queue backend to SQS for worker/query containers via env vars:

- `CINA__INGESTION__QUEUE__BACKEND=sqs`
- `SQS_QUEUE_URL` and `SQS_DLQ_URL`

## 6. Smoke Tests

- Query service health:

```bash
curl http://<alb-dns>/health
```

- Streaming query:

```bash
curl -N -X POST "http://<alb-dns>/v1/query" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <cina_sk_...>" \
  -d '{"query":"Recent treatment options for metastatic breast cancer"}'
```

- Observe ECS logs in CloudWatch log groups:

- `/ecs/cina/query`
- `/ecs/cina/ingestion`

## 7. Cost and Teardown

When demo evidence is complete:

```bash
cd infra/terraform
terraform destroy
```

Record final resources/cost notes in `docs/terraform/cost.md`.
