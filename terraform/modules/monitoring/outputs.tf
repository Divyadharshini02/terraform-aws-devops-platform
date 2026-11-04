output "alb_logs_bucket" {
  description = "Name of the S3 bucket for load balancer access logs. Read from the bucket policy so anything using it waits until log delivery is allowed."
  value       = aws_s3_bucket_policy.alb_logs.bucket
}

output "app_log_group_name" {
  description = "CloudWatch log group for application logs."
  value       = aws_cloudwatch_log_group.app.name
}

output "alarm_topic_arn" {
  description = "ARN of the SNS topic that receives alarm notifications."
  value       = aws_sns_topic.alarms.arn
}

output "alarm_names" {
  description = "Names of the CloudWatch alarms."
  value       = [for a in aws_cloudwatch_metric_alarm.this : a.alarm_name]
}
