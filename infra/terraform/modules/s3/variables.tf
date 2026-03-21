variable "name_prefix" {
  type = string
}

variable "document_bucket_name" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
