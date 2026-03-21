resource "aws_s3_bucket" "documents" {
  bucket = var.document_bucket_name

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-documents"
  })
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    id     = "expire-documents-after-7-days"
    status = "Enabled"

    filter {}

    expiration {
      days = 7
    }
  }
}
