data "aws_region" "current" {}

data "aws_ecr_repository" "query" {
  name = "${var.name_prefix}-query"
}

data "aws_ecr_repository" "ingestion" {
  name = "${var.name_prefix}-ingestion"
}

resource "aws_ecs_cluster" "this" {
  name = "${var.name_prefix}-cluster"

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-cluster"
  })
}

resource "aws_lb" "this" {
  name               = "${var.name_prefix}-alb"
  load_balancer_type = "application"
  subnets            = var.public_subnet_ids
  security_groups    = [var.alb_security_group_id]

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-alb"
  })
}

resource "aws_lb_target_group" "query" {
  name        = "${var.name_prefix}-query-tg"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    path                = "/health"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 2
    matcher             = "200-399"
  }

}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.query.arn
  }
}

locals {
  common_env = [
    { name = "DATABASE_URL", value = var.database_url },
    { name = "REDIS_URL", value = var.redis_url },
    { name = "SQS_QUEUE_URL", value = var.sqs_queue_url },
    { name = "SQS_DLQ_URL", value = var.sqs_dlq_url },
    { name = "OPENAI_API_KEY", value = var.openai_api_key },
    { name = "ANTHROPIC_API_KEY", value = var.anthropic_api_key },
    { name = "CINA__INGESTION__QUEUE__BACKEND", value = "sqs" },
    { name = "CINA__INGESTION__QUEUE__NAME", value = "cina-ingestion" },
  ]
}

resource "aws_ecs_task_definition" "query" {
  family                   = "${var.name_prefix}-query"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "query"
      image     = "${data.aws_ecr_repository.query.repository_url}:${var.query_image_tag}"
      essential = true
      portMappings = [
        {
          containerPort = 8000
          protocol      = "tcp"
        }
      ]
      command     = ["uvicorn", "cina.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
      environment = local.common_env
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = "/ecs/${var.name_prefix}/query"
          awslogs-region        = data.aws_region.current.name
          awslogs-stream-prefix = "ecs"
          awslogs-create-group  = "true"
        }
      }
    }
  ])
}

resource "aws_ecs_task_definition" "ingestion" {
  family                   = "${var.name_prefix}-ingestion"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name        = "ingestion"
      image       = "${data.aws_ecr_repository.ingestion.repository_url}:${var.ingestion_image_tag}"
      essential   = true
      command     = ["python", "-m", "cina", "ingest", "worker", "--batch-size", "64"]
      environment = local.common_env
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = "/ecs/${var.name_prefix}/ingestion"
          awslogs-region        = data.aws_region.current.name
          awslogs-stream-prefix = "ecs"
          awslogs-create-group  = "true"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "query" {
  name            = "${var.name_prefix}-query"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.query.arn
  desired_count   = var.query_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.ecs_security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.query.arn
    container_name   = "query"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.http]
}

resource "aws_ecs_service" "ingestion" {
  name            = "${var.name_prefix}-ingestion"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.ingestion.arn
  desired_count   = var.ingestion_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.ecs_security_group_id]
    assign_public_ip = false
  }
}
