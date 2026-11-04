# ---------- Naming and placement ----------

variable "project_name" {
  description = "Short project name used in every resource name."
  type        = string
  default     = "taskapi"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,15}$", var.project_name))
    error_message = "project_name must be 3-16 characters: lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "Environment name. Only dev exists in this project."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev"], var.environment)
    error_message = "environment must be \"dev\"."
  }
}

variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must look like us-east-1 or eu-west-2."
  }
}

variable "owner" {
  description = "Owner tag value (team or person responsible)."
  type        = string
  default     = "platform-portfolio"
}

# ---------- Networking ----------

variable "vpc_cidr" {
  description = "IPv4 CIDR block of the VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones (2 or 3)."
  type        = number
  default     = 2
}

variable "enable_nat_gateway" {
  description = "Create a NAT gateway for private subnet internet access (billed hourly, off by default)."
  type        = bool
  default     = false
}

variable "allowed_ingress_cidrs" {
  description = "Client CIDR blocks allowed to reach the load balancer."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "certificate_arn" {
  description = "ACM certificate ARN to enable HTTPS. null = HTTP only."
  type        = string
  default     = null
}

# ---------- Compute and scaling ----------

variable "instance_type" {
  description = "EC2 instance type for app instances."
  type        = string
  default     = "t3.micro"
}

variable "app_container_image" {
  description = "Container image of the Task API. Empty = instances boot without the app."
  type        = string
  default     = ""
}

variable "asg_min_size" {
  description = "Minimum number of app instances."
  type        = number
  default     = 1
}

variable "asg_max_size" {
  description = "Maximum number of app instances."
  type        = number
  default     = 3
}

variable "asg_desired_capacity" {
  description = "Initial number of app instances."
  type        = number
  default     = 1
}

variable "target_requests_per_instance" {
  description = "Requests per instance per minute that target tracking aims for (600 = 10 req/s, the M7 threshold)."
  type        = number
  default     = 600
}

variable "alb_deletion_protection" {
  description = "Protect the load balancer from deletion."
  type        = bool
  default     = true
}

# ---------- Database ----------

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_multi_az" {
  description = "Run the database with a standby in a second AZ."
  type        = bool
  default     = false
}

variable "db_deletion_protection" {
  description = "Protect the database from deletion."
  type        = bool
  default     = true
}

# ---------- Monitoring ----------

variable "log_retention_days" {
  description = "Retention for application and VPC flow logs, in days."
  type        = number
  default     = 30
}

variable "alarm_email" {
  description = "Optional e-mail for alarm notifications. null = none."
  type        = string
  default     = null
}
