variable "aws_region" {
  description = "Region of your Kiro profile. The report bucket and every resource here must be in this Region."
  type        = string
  default     = "us-east-1"
}

variable "aws_account_id" {
  description = "ID of the AWS account where users are subscribed to Kiro (it appears in the report path)"
  type        = string
}

variable "project_name" {
  description = "Prefix for the names of the resources this stack creates"
  type        = string
  default     = "kiro-user-report-dashboard"
}

variable "s3_bucket_name" {
  description = "Bucket and prefix where Kiro delivers the user activity reports, without s3:// (e.g. amzn-s3-demo-bucket/user-activity)"
  type        = string
}

variable "glue_database_name" {
  description = "Glue database name"
  type        = string
  default     = "kiro_user_report"
}

variable "glue_crawler_schedule" {
  description = "Cron expression for the Glue crawler. Kiro writes each day's report at about 02:00 UTC."
  type        = string
  default     = "cron(0 3 * * ? *)"
}

variable "identity_store_id" {
  description = "IAM Identity Center identity store ID (e.g. d-1234567890), used for user names and for hiding removed users. Empty disables both."
  type        = string
  default     = ""
}

variable "identity_store_region" {
  description = "Region of the IAM Identity Center instance, if it differs from aws_region. Empty means aws_region."
  type        = string
  default     = ""
}

variable "local_iam_user_name" {
  description = "Existing IAM user that runs the dashboard; the read-only access policy is attached to it. Empty skips the attachment."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags applied to every resource this stack creates"
  type        = map(string)
  default = {
    Project   = "Kiro Usage Dashboard"
    ManagedBy = "Terraform"
  }
}
