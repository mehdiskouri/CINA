resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name_prefix}-ingestion-dlq"
  message_retention_seconds = 1209600

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-ingestion-dlq"
  })
}

resource "aws_sqs_queue" "main" {
  name                       = "${var.name_prefix}-ingestion"
  visibility_timeout_seconds = 300
  receive_wait_time_seconds  = 20
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-ingestion"
  })
}
