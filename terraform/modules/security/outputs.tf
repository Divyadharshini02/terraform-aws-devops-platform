output "alb_security_group_id" {
  description = "Security group ID for the load balancer."
  value       = aws_security_group.alb.id
}

output "app_security_group_id" {
  description = "Security group ID for the app instances."
  value       = aws_security_group.app.id
}

output "db_security_group_id" {
  description = "Security group ID for the database."
  value       = aws_security_group.db.id
}

output "app_instance_profile_name" {
  description = "Name of the IAM instance profile attached to app instances."
  value       = aws_iam_instance_profile.app.name
}

output "app_role_arn" {
  description = "ARN of the IAM role used by app instances."
  value       = aws_iam_role.app.arn
}
