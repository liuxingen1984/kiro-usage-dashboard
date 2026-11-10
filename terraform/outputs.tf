output "aws_region" {
  description = "AWS region"
  value       = var.aws_region
}

output "glue_database_name" {
  description = "Name of the Glue database"
  value       = aws_glue_catalog_database.analytics_db.name
}

output "glue_crawler_name" {
  description = "Name of the Glue crawler"
  value       = aws_glue_crawler.analytics_crawler.name
}

output "athena_workgroup_name" {
  description = "Athena workgroup the dashboard must use (its access policy allows only this one)"
  value       = aws_athena_workgroup.analytics_workgroup.name
}

output "athena_output_location" {
  description = "S3 location for Athena query results"
  value       = "s3://${aws_s3_bucket.athena_results.bucket}/"
}

output "identity_store_id" {
  description = "IAM Identity Center Identity Store ID"
  value       = var.identity_store_id
}

output "identity_store_region" {
  description = "Region of the IAM Identity Center instance"
  value       = var.identity_store_region == "" ? var.aws_region : var.identity_store_region
}

output "dashboard_access_policy_arn" {
  description = "Read-only policy the dashboard needs; attach it to the role or permission set that runs the dashboard"
  value       = aws_iam_policy.athena_access_policy.arn
}

output "s3_data_path" {
  description = "Full S3 data path including account ID and region"
  value       = "s3://${local.s3_data_path}/"
}
