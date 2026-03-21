# Phase 4 Terraform (AWS Deployment Proof)

## Scope

This stack provisions the Phase 4 AWS proof environment:
- VPC with public/private subnets
- RDS PostgreSQL
- ElastiCache Redis
- SQS + DLQ
- S3 document bucket
- IAM roles for ECS
- ECS Fargate services (query API + ingestion worker)
- ALB for `/health` and `/v1/query`

## Usage

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with real values
terraform init
terraform fmt -recursive
terraform validate
terraform plan -out=tfplan
terraform apply tfplan
```

Destroy when done:

```bash
terraform destroy
```

## Notes

- `db_password`, `openai_api_key`, and `anthropic_api_key` are sensitive variables.
- Query and ingestion images are expected in the ECR repos created by this stack.
- Use `scripts/ecr_push.sh` before `terraform apply` if using `latest` image tags.
