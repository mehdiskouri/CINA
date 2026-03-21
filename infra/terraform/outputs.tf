output "alb_dns_name" {
  value       = module.ecs.alb_dns_name
  description = "Public DNS name for query API"
}

output "query_ecr_repository_url" {
  value       = module.ecs.query_repository_url
  description = "ECR repository URL for query image"
}

output "ingestion_ecr_repository_url" {
  value       = module.ecs.ingestion_repository_url
  description = "ECR repository URL for ingestion image"
}

output "sqs_queue_url" {
  value       = module.sqs.queue_url
  description = "Main ingestion queue URL"
}

output "sqs_dlq_url" {
  value       = module.sqs.dlq_url
  description = "Dead-letter queue URL"
}

output "rds_endpoint" {
  value       = module.rds.address
  description = "RDS endpoint"
}

output "redis_endpoint" {
  value       = module.elasticache.endpoint
  description = "ElastiCache endpoint"
}
