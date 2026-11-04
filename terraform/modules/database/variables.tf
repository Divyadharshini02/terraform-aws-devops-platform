variable "name" {
  description = "Name prefix for database resources, e.g. \"taskapi-dev\"."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name))
    error_message = "name must be 3-31 characters: lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "private_subnet_ids" {
  description = "Private subnets for the DB subnet group (at least two AZs)."
  type        = list(string)

  validation {
    condition     = length(var.private_subnet_ids) >= 2
    error_message = "An RDS subnet group needs subnets in at least two availability zones."
  }
}

variable "db_security_group_id" {
  description = "Security group that only allows PostgreSQL from the app instances."
  type        = string
}

variable "engine_major_version" {
  description = "PostgreSQL major version (local Docker setup uses postgres:16)."
  type        = string
  default     = "16"

  validation {
    condition     = contains(["15", "16", "17"], var.engine_major_version)
    error_message = "engine_major_version must be 15, 16 or 17."
  }
}

variable "instance_class" {
  description = "RDS instance class. Small burstable classes keep a demo environment cheap."
  type        = string
  default     = "db.t4g.micro"

  validation {
    condition     = can(regex("^db\\.(t4g|t3|m6g|m7g|r6g|r7g)\\.[a-z0-9]+$", var.instance_class))
    error_message = "instance_class must look like db.t4g.micro (families t4g, t3, m6g, m7g, r6g, r7g)."
  }
}

variable "allocated_storage_gb" {
  description = "Initial storage in GiB."
  type        = number
  default     = 20

  validation {
    condition     = var.allocated_storage_gb >= 20 && var.allocated_storage_gb <= 1000
    error_message = "allocated_storage_gb must be between 20 and 1000."
  }
}

variable "max_allocated_storage_gb" {
  description = "Upper limit for storage autoscaling in GiB (must be >= allocated_storage_gb)."
  type        = number
  default     = 50

  validation {
    condition     = var.max_allocated_storage_gb >= var.allocated_storage_gb
    error_message = "max_allocated_storage_gb must be >= allocated_storage_gb."
  }
}

variable "db_name" {
  description = "Name of the application database (local setup: tasks)."
  type        = string
  default     = "tasks"

  validation {
    condition     = can(regex("^[a-z][a-z0-9_]{0,62}$", var.db_name))
    error_message = "db_name must start with a lowercase letter and contain only lowercase letters, digits and underscores."
  }
}

variable "master_username" {
  description = "Master user name. The password is generated and stored by AWS Secrets Manager, never by Terraform."
  type        = string
  default     = "taskadmin"

  validation {
    condition     = can(regex("^[a-z][a-z0-9_]{2,15}$", var.master_username)) && !contains(["admin", "postgres", "rdsadmin", "root"], var.master_username)
    error_message = "master_username must be 3-16 lowercase characters and not a reserved name (admin, postgres, rdsadmin, root)."
  }
}

variable "multi_az" {
  description = "Run a standby replica in a second AZ. Off by default for cost; enable for production."
  type        = bool
  default     = false
}

variable "backup_retention_days" {
  description = "Days of automated backups to keep."
  type        = number
  default     = 7

  validation {
    condition     = var.backup_retention_days >= 1 && var.backup_retention_days <= 35
    error_message = "backup_retention_days must be between 1 and 35."
  }
}

variable "deletion_protection" {
  description = "Prevent the database from being deleted."
  type        = bool
  default     = true
}

variable "skip_final_snapshot" {
  description = "Skip the final snapshot when the database is deleted. false keeps a last backup."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Extra tags for resources in this module."
  type        = map(string)
  default     = {}
}
