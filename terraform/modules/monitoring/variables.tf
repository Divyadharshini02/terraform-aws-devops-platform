variable "name" {
  description = "Name prefix for monitoring resources, e.g. \"taskapi-dev\"."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name))
    error_message = "name must be 3-31 characters: lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "log_retention_days" {
  description = "CloudWatch retention for application logs, in days."
  type        = number
  default     = 30

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365], var.log_retention_days)
    error_message = "log_retention_days must be a CloudWatch-supported retention value (1-365 days)."
  }
}

variable "access_log_expiration_days" {
  description = "Days after which load balancer access logs are deleted from S3."
  type        = number
  default     = 90

  validation {
    condition     = var.access_log_expiration_days >= 7 && var.access_log_expiration_days <= 3650
    error_message = "access_log_expiration_days must be between 7 and 3650."
  }
}

variable "alarm_email" {
  description = "Optional e-mail address subscribed to alarm notifications (AWS sends a confirmation mail). null = no subscription."
  type        = string
  default     = null

  validation {
    condition     = var.alarm_email == null || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alarm_email))
    error_message = "alarm_email must be null or a valid e-mail address."
  }
}

# ---------- Resources to monitor (outputs of the compute and database modules) ----------

variable "alb_arn_suffix" {
  description = "ARN suffix of the load balancer (CloudWatch dimension \"LoadBalancer\")."
  type        = string
}

variable "target_group_arn_suffix" {
  description = "ARN suffix of the target group (CloudWatch dimension \"TargetGroup\")."
  type        = string
}

variable "db_instance_identifier" {
  description = "Identifier of the RDS instance (CloudWatch dimension \"DBInstanceIdentifier\")."
  type        = string
}

# ---------- Alarm thresholds ----------

variable "error_5xx_threshold" {
  description = "Alarm when 5xx responses per minute exceed this value for 5 consecutive minutes."
  type        = number
  default     = 5

  validation {
    condition     = var.error_5xx_threshold >= 0
    error_message = "error_5xx_threshold must be >= 0."
  }
}

variable "p95_latency_threshold_seconds" {
  description = "Alarm when p95 target response time exceeds this many seconds (0.5 s matches the M6 k6 threshold)."
  type        = number
  default     = 0.5

  validation {
    condition     = var.p95_latency_threshold_seconds > 0 && var.p95_latency_threshold_seconds <= 60
    error_message = "p95_latency_threshold_seconds must be between 0 and 60."
  }
}

variable "db_cpu_threshold_percent" {
  description = "Alarm when database CPU stays above this percentage."
  type        = number
  default     = 80

  validation {
    condition     = var.db_cpu_threshold_percent > 0 && var.db_cpu_threshold_percent <= 100
    error_message = "db_cpu_threshold_percent must be between 1 and 100."
  }
}

variable "db_free_storage_threshold_bytes" {
  description = "Alarm when free database storage drops below this many bytes (default 2 GiB)."
  type        = number
  default     = 2147483648

  validation {
    condition     = var.db_free_storage_threshold_bytes > 0
    error_message = "db_free_storage_threshold_bytes must be greater than 0."
  }
}

variable "tags" {
  description = "Extra tags for resources in this module."
  type        = map(string)
  default     = {}
}
