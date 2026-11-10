terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = var.tags
  }
}

# Construct the full S3 data path from components
# Pattern: {s3_bucket_name}/AWSLogs/{account_id}/KiroLogs/user_report/{region}
locals {
  s3_data_path = "${var.s3_bucket_name}/AWSLogs/${var.aws_account_id}/KiroLogs/user_report/${var.aws_region}"
  # Extract the actual bucket name (before the first slash) for ARN-based policies
  s3_bucket_only = split("/", var.s3_bucket_name)[0]
}

# S3 Bucket for Athena query results. It holds only disposable query output,
# so `terraform destroy` may empty and delete it.
resource "aws_s3_bucket" "athena_results" {
  bucket        = "${var.project_name}-athena-results-${var.aws_account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "athena_results_public_access_block" {
  bucket = aws_s3_bucket.athena_results.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "athena_results_encryption" {
  bucket = aws_s3_bucket.athena_results.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "athena_results_versioning" {
  bucket = aws_s3_bucket.athena_results.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "athena_results_lifecycle" {
  bucket = aws_s3_bucket.athena_results.id

  rule {
    id     = "delete-old-results"
    status = "Enabled"

    filter {
      prefix = ""
    }

    expiration {
      days = 7
    }

    # The bucket is versioned, so also remove the versions that expiration leaves behind.
    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }
}

# Glue Database
resource "aws_glue_catalog_database" "analytics_db" {
  name        = var.glue_database_name
  description = "Database for Kiro analytics data"
}

# IAM Role for Glue Crawler
resource "aws_iam_role" "glue_crawler_role" {
  name = "${var.project_name}-glue-crawler-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "glue.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "glue_service_policy" {
  role       = aws_iam_role.glue_crawler_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

resource "aws_iam_role_policy" "glue_s3_policy" {
  name = "${var.project_name}-glue-s3-policy"
  role = aws_iam_role.glue_crawler_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          "arn:aws:s3:::${local.s3_bucket_only}",
          "arn:aws:s3:::${local.s3_data_path}/*"
        ]
      }
    ]
  })
}

# Glue Crawler
resource "aws_glue_crawler" "analytics_crawler" {
  name          = "${var.project_name}-crawler"
  role          = aws_iam_role.glue_crawler_role.arn
  database_name = aws_glue_catalog_database.analytics_db.name

  s3_target {
    path = "s3://${local.s3_data_path}/"
  }

  schema_change_policy {
    delete_behavior = "LOG"
    update_behavior = "UPDATE_IN_DATABASE"
  }

  configuration = jsonencode({
    Version = 1.0
    CrawlerOutput = {
      Partitions = { AddOrUpdateBehavior = "InheritFromTable" }
    }
  })

  schedule = var.glue_crawler_schedule
}

# Athena Workgroup
resource "aws_athena_workgroup" "analytics_workgroup" {
  name = "${var.project_name}-workgroup"

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true

    result_configuration {
      output_location = "s3://${aws_s3_bucket.athena_results.bucket}/"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}

# IAM Policy for Athena access (for application)
resource "aws_iam_policy" "athena_access_policy" {
  name        = "${var.project_name}-athena-access"
  description = "Policy for accessing Athena, Glue, S3, and Identity Store resources"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StopQueryExecution",
          "athena:GetWorkGroup"
        ]
        Resource = [
          aws_athena_workgroup.analytics_workgroup.arn,
          "arn:aws:athena:${var.aws_region}:*:workgroup/${aws_athena_workgroup.analytics_workgroup.name}"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetTable",
          "glue:GetTables",
          "glue:GetPartitions"
        ]
        Resource = [
          "arn:aws:glue:${var.aws_region}:*:catalog",
          "arn:aws:glue:${var.aws_region}:*:database/${aws_glue_catalog_database.analytics_db.name}",
          "arn:aws:glue:${var.aws_region}:*:table/${aws_glue_catalog_database.analytics_db.name}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          "arn:aws:s3:::${local.s3_bucket_only}",
          "arn:aws:s3:::${local.s3_data_path}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.athena_results.arn,
          "${aws_s3_bucket.athena_results.arn}/*"
        ]
      },
      {
        # User names, and whether a user was removed from or disabled in IAM Identity Center
        Effect   = "Allow"
        Action   = ["identitystore:DescribeUser"]
        Resource = "*"
      }
    ]
  })
}

# Attach the policy to the existing IAM user that runs the dashboard.
# Leave local_iam_user_name empty to skip this and attach the policy
# (output dashboard_access_policy_arn) to a role or permission set instead.
resource "aws_iam_user_policy_attachment" "local_user_athena_access" {
  count      = var.local_iam_user_name == "" ? 0 : 1
  user       = var.local_iam_user_name
  policy_arn = aws_iam_policy.athena_access_policy.arn
}
