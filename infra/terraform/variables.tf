variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Name prefix for deployed resources"
  type        = string
  default     = "cina"
}

variable "db_name" {
  description = "RDS database name"
  type        = string
  default     = "cina"
}

variable "db_username" {
  description = "RDS master username"
  type        = string
  default     = "cina"
}

variable "db_password" {
  description = "RDS master password"
  type        = string
  sensitive   = true
}

variable "document_bucket_name" {
  description = "Globally unique S3 bucket name for source docs"
  type        = string
}

variable "anthropic_api_key" {
  description = "Anthropic API key injected into ECS tasks"
  type        = string
  sensitive   = true
}

variable "openai_api_key" {
  description = "OpenAI API key injected into ECS tasks"
  type        = string
  sensitive   = true
}

variable "query_image_tag" {
  description = "Image tag for query service"
  type        = string
  default     = "latest"
}

variable "ingestion_image_tag" {
  description = "Image tag for ingestion service"
  type        = string
  default     = "latest"
}

variable "query_desired_count" {
  description = "Desired running tasks for query service"
  type        = number
  default     = 2
}

variable "ingestion_desired_count" {
  description = "Desired running tasks for ingestion worker"
  type        = number
  default     = 1
}

variable "tags" {
  description = "Common tags applied to resources"
  type        = map(string)
  default = {
    project     = "cina"
    environment = "phase4"
  }
}
