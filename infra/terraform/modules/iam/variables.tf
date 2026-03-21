variable "name_prefix" {
  type = string
}

variable "queue_arn" {
  type = string
}

variable "dlq_arn" {
  type = string
}

variable "bucket_arn" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
