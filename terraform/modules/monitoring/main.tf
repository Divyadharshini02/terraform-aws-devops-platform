# Monitoring: log storage (S3 bucket for load balancer access logs and a
# CloudWatch log group for the app), an alert topic, and CloudWatch alarms.
# This is the AWS counterpart of the local Prometheus + Grafana stack.

data "aws_elb_service_account" "current" {}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

# ---------- S3 bucket for load balancer access logs ----------

resource "aws_s3_bucket" "alb_logs" {
  #checkov:skip=CKV_AWS_145:ALB access logs support only SSE-S3 (AES256), which is enabled. SSE-KMS is not supported for this use.
  #checkov:skip=CKV_AWS_144:Cross-region replication is not justified for short-lived access logs that expire automatically.
  #checkov:skip=CKV2_AWS_62:No consumer for log-arrival events, so event notifications would have no target.
  #checkov:skip=CKV_AWS_18:Access logging for the log bucket itself would require another log bucket.
  # bucket_prefix adds a random suffix, so the globally unique name needs no account ID.
  bucket_prefix = "${var.name}-alb-logs-"
  force_destroy = false

  tags = merge(var.tags, { Name = "${var.name}-alb-logs" })
}

resource "aws_s3_bucket_public_access_block" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    object_ownership = "BucketOwnerEnforced" # disables ACLs entirely
  }
}

resource "aws_s3_bucket_versioning" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

# ALB access logs only support SSE-S3 (AES256), not customer-managed KMS keys.
resource "aws_s3_bucket_server_side_encryption_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    id     = "expire-access-logs"
    status = "Enabled"

    filter {}

    expiration {
      days = var.access_log_expiration_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "alb_logs" {
  # Older regions deliver ALB logs from the regional ELB account.
  statement {
    sid       = "AllowElbAccountLogDelivery"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.alb_logs.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"]

    principals {
      type        = "AWS"
      identifiers = [data.aws_elb_service_account.current.arn]
    }
  }

  # Newer regions deliver ALB logs from this service principal.
  statement {
    sid       = "AllowLogDeliveryService"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.alb_logs.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"]

    principals {
      type        = "Service"
      identifiers = ["logdelivery.elasticloadbalancing.amazonaws.com"]
    }
  }

  # Reject any request that is not encrypted in transit.
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.alb_logs.arn, "${aws_s3_bucket.alb_logs.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  policy = data.aws_iam_policy_document.alb_logs.json

  depends_on = [aws_s3_bucket_public_access_block.alb_logs]
}

# ---------- Application logs ----------

resource "aws_cloudwatch_log_group" "app" {
  #checkov:skip=CKV_AWS_158:Encrypted by default with AWS-owned keys. A customer-managed KMS key adds a monthly charge in this dev design.
  name              = "/app/${var.name}"
  retention_in_days = var.log_retention_days

  tags = var.tags
}

# ---------- Alert topic ----------

resource "aws_sns_topic" "alarms" {
  name              = "${var.name}-alarms"
  kms_master_key_id = "alias/aws/sns" # AWS-managed key: encrypted at rest at no extra key cost

  tags = var.tags
}

data "aws_iam_policy_document" "alarms_topic" {
  statement {
    sid       = "AllowCloudWatchAlarmsFromThisAccount"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alarms.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${data.aws_partition.current.partition}:cloudwatch:*:${data.aws_caller_identity.current.account_id}:alarm:*"]
    }
  }
}

resource "aws_sns_topic_policy" "alarms" {
  arn    = aws_sns_topic.alarms.arn
  policy = data.aws_iam_policy_document.alarms_topic.json
}

resource "aws_sns_topic_subscription" "email" {
  count = var.alarm_email == null ? 0 : 1

  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# ---------- Alarms ----------

locals {
  # tomap() gives every alarm the same dimensions type, map(string), so the
  # alarm definitions below can be iterated with for_each.
  alb_dimensions = tomap({ LoadBalancer = var.alb_arn_suffix })
  tg_dimensions  = tomap({ LoadBalancer = var.alb_arn_suffix, TargetGroup = var.target_group_arn_suffix })
  db_dimensions  = tomap({ DBInstanceIdentifier = var.db_instance_identifier })

  # One entry per alarm; the for_each below turns each into a CloudWatch alarm.
  alarms = {
    alb-target-5xx = {
      description = "App instances returned 5xx responses"
      namespace   = "AWS/ApplicationELB"
      metric      = "HTTPCode_Target_5XX_Count"
      statistic   = "Sum"
      percentile  = null
      period      = 60
      periods     = 5
      comparison  = "GreaterThanThreshold"
      threshold   = var.error_5xx_threshold
      dimensions  = local.alb_dimensions
    }
    alb-elb-5xx = {
      description = "Load balancer itself returned 5xx (e.g. 502 when a target fails mid-request)"
      namespace   = "AWS/ApplicationELB"
      metric      = "HTTPCode_ELB_5XX_Count"
      statistic   = "Sum"
      percentile  = null
      period      = 60
      periods     = 5
      comparison  = "GreaterThanThreshold"
      threshold   = var.error_5xx_threshold
      dimensions  = local.alb_dimensions
    }
    unhealthy-targets = {
      description = "At least one target failed its health check"
      namespace   = "AWS/ApplicationELB"
      metric      = "UnHealthyHostCount"
      statistic   = "Maximum"
      percentile  = null
      period      = 60
      periods     = 3
      comparison  = "GreaterThanThreshold"
      threshold   = 0
      dimensions  = local.tg_dimensions
    }
    p95-latency = {
      description = "p95 target response time is above the latency objective"
      namespace   = "AWS/ApplicationELB"
      metric      = "TargetResponseTime"
      statistic   = null
      percentile  = "p95"
      period      = 60
      periods     = 5
      comparison  = "GreaterThanThreshold"
      threshold   = var.p95_latency_threshold_seconds
      dimensions  = local.alb_dimensions
    }
    db-cpu-high = {
      description = "Database CPU is consistently high"
      namespace   = "AWS/RDS"
      metric      = "CPUUtilization"
      statistic   = "Average"
      percentile  = null
      period      = 300
      periods     = 3
      comparison  = "GreaterThanThreshold"
      threshold   = var.db_cpu_threshold_percent
      dimensions  = local.db_dimensions
    }
    db-storage-low = {
      description = "Database free storage is running low"
      namespace   = "AWS/RDS"
      metric      = "FreeStorageSpace"
      statistic   = "Minimum"
      percentile  = null
      period      = 300
      periods     = 1
      comparison  = "LessThanThreshold"
      threshold   = var.db_free_storage_threshold_bytes
      dimensions  = local.db_dimensions
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each = local.alarms

  alarm_name          = "${var.name}-${each.key}"
  alarm_description   = each.value.description
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = each.value.statistic
  extended_statistic  = each.value.percentile
  period              = each.value.period
  evaluation_periods  = each.value.periods
  comparison_operator = each.value.comparison
  threshold           = each.value.threshold
  dimensions          = each.value.dimensions
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]

  tags = var.tags
}
