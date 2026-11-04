output "vpc_id" {
  description = "ID of the VPC."
  value       = module.networking.vpc_id
}

output "alb_dns_name" {
  description = "Public DNS name of the load balancer (the AWS counterpart of http://localhost:8081)."
  value       = module.compute.alb_dns_name
}

output "autoscaling_group_name" {
  description = "Name of the Auto Scaling group running the app."
  value       = module.compute.autoscaling_group_name
}

output "db_endpoint" {
  description = "Database endpoint, reachable only from app instances."
  value       = module.database.db_endpoint
}

output "db_master_user_secret_arn" {
  description = "ARN of the AWS-managed secret holding the database master password."
  value       = module.database.master_user_secret_arn
}

output "alb_logs_bucket" {
  description = "S3 bucket receiving load balancer access logs."
  value       = module.monitoring.alb_logs_bucket
}

output "alarm_topic_arn" {
  description = "SNS topic that receives CloudWatch alarm notifications."
  value       = module.monitoring.alarm_topic_arn
}
