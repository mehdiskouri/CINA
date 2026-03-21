output "alb_dns_name" {
  value = aws_lb.this.dns_name
}

output "query_repository_url" {
  value = data.aws_ecr_repository.query.repository_url
}

output "ingestion_repository_url" {
  value = data.aws_ecr_repository.ingestion.repository_url
}
