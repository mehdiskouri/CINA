module "vpc" {
  source      = "./modules/vpc"
  name_prefix = var.name_prefix
  tags        = var.tags
}

module "sqs" {
  source      = "./modules/sqs"
  name_prefix = var.name_prefix
  tags        = var.tags
}

module "rds" {
  source         = "./modules/rds"
  name_prefix    = var.name_prefix
  subnet_ids     = module.vpc.private_subnet_ids
  security_group = module.vpc.rds_security_group_id
  db_name        = var.db_name
  db_username    = var.db_username
  db_password    = var.db_password
  tags           = var.tags
}

module "elasticache" {
  source         = "./modules/elasticache"
  name_prefix    = var.name_prefix
  subnet_ids     = module.vpc.private_subnet_ids
  security_group = module.vpc.redis_security_group_id
  tags           = var.tags
}

module "iam" {
  source      = "./modules/iam"
  name_prefix = var.name_prefix
  queue_arn   = module.sqs.queue_arn
  dlq_arn     = module.sqs.dlq_arn
  bucket_arn  = "arn:aws:s3:::${var.document_bucket_name}"
  tags        = var.tags
}

module "ecs" {
  source                  = "./modules/ecs"
  name_prefix             = var.name_prefix
  vpc_id                  = module.vpc.vpc_id
  public_subnet_ids       = module.vpc.public_subnet_ids
  private_subnet_ids      = module.vpc.private_subnet_ids
  alb_security_group_id   = module.vpc.alb_security_group_id
  ecs_security_group_id   = module.vpc.ecs_security_group_id
  execution_role_arn      = module.iam.execution_role_arn
  task_role_arn           = module.iam.task_role_arn
  database_url            = "postgresql://${var.db_username}:${var.db_password}@${module.rds.address}:${module.rds.port}/${var.db_name}"
  redis_url               = "redis://${module.elasticache.endpoint}:${module.elasticache.port}/0"
  sqs_queue_url           = module.sqs.queue_url
  sqs_dlq_url             = module.sqs.dlq_url
  anthropic_api_key       = var.anthropic_api_key
  openai_api_key          = var.openai_api_key
  query_image_tag         = var.query_image_tag
  ingestion_image_tag     = var.ingestion_image_tag
  query_desired_count     = var.query_desired_count
  ingestion_desired_count = var.ingestion_desired_count
  tags                    = var.tags
}
