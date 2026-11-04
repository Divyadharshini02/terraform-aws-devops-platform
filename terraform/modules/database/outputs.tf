output "db_instance_identifier" {
  description = "Identifier of the RDS instance."
  value       = aws_db_instance.this.identifier
}

output "db_endpoint" {
  description = "Connection endpoint (host:port) of the database. Reachable only from the app security group."
  value       = aws_db_instance.this.endpoint
}

output "db_name" {
  description = "Name of the application database."
  value       = aws_db_instance.this.db_name
}

# The secret's ARN is an identifier, not the password itself.
output "master_user_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the master password (managed by AWS)."
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}
