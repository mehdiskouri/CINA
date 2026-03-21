variable "name_prefix" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "security_group" {
  type = string
}

variable "node_type" {
  type    = string
  default = "cache.t3.micro"
}

variable "tags" {
  type    = map(string)
  default = {}
}
