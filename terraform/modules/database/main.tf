# Database: managed PostgreSQL in the private subnets (the AWS counterpart of
# the local Postgres container with its pgdata volume).

resource "aws_db_subnet_group" "this" {
  name_prefix = "${var.name}-db-"
  description = "Private subnets for ${var.name} PostgreSQL"
  subnet_ids  = var.private_subnet_ids

  tags = merge(var.tags, { Name = "${var.name}-db-subnets" })
}

# Server-side settings: refuse unencrypted client connections and log
# connections for auditing.
resource "aws_db_parameter_group" "this" {
  name_prefix = "${var.name}-pg${var.engine_major_version}-"
  family      = "postgres${var.engine_major_version}"
  description = "PostgreSQL ${var.engine_major_version} settings for ${var.name}"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  parameter {
    name  = "log_connections"
    value = "1"
  }

  tags = var.tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_db_instance" "this" {
  #checkov:skip=CKV_AWS_157:Single-AZ by default to avoid standby cost. The multi_az variable enables it for production.
  #checkov:skip=CKV_AWS_118:Enhanced monitoring needs an extra IAM role and CloudWatch ingestion cost. Basic RDS metrics feed the CPU and storage alarms.
  #checkov:skip=CKV_AWS_353:Performance Insights not needed for this demo. Support for the db.t4g.micro class was not verified.
  identifier     = "${var.name}-db"
  engine         = "postgres"
  engine_version = var.engine_major_version
  instance_class = var.instance_class

  db_name  = var.db_name
  username = var.master_username
  # AWS generates the master password and keeps it in Secrets Manager:
  # no password in code, variables or Terraform state.
  manage_master_user_password = true

  allocated_storage     = var.allocated_storage_gb
  max_allocated_storage = var.max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [var.db_security_group_id]
  parameter_group_name   = aws_db_parameter_group.this.name
  publicly_accessible    = false
  multi_az               = var.multi_az

  iam_database_authentication_enabled = true
  enabled_cloudwatch_logs_exports     = ["postgresql", "upgrade"]

  backup_retention_period    = var.backup_retention_days
  copy_tags_to_snapshot      = true
  auto_minor_version_upgrade = true
  deletion_protection        = var.deletion_protection
  skip_final_snapshot        = var.skip_final_snapshot
  final_snapshot_identifier  = var.skip_final_snapshot ? null : "${var.name}-db-final"

  tags = merge(var.tags, { Name = "${var.name}-db" })
}
