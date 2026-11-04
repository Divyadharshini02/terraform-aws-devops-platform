variable "name" {
  description = "Name prefix for compute resources, e.g. \"taskapi-dev\". Kept short because ALB and target group names are limited to 32 characters."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,24}$", var.name))
    error_message = "name must be 3-25 characters: lowercase letters, digits and hyphens, starting with a letter."
  }
}

# ---------- Network inputs (from the networking and security modules) ----------

variable "vpc_id" {
  description = "ID of the VPC."
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnets for the load balancer (at least two AZs)."
  type        = list(string)

  validation {
    condition     = length(var.public_subnet_ids) >= 2
    error_message = "An Application Load Balancer needs subnets in at least two availability zones."
  }
}

variable "private_subnet_ids" {
  description = "Private subnets for the app instances."
  type        = list(string)

  validation {
    condition     = length(var.private_subnet_ids) >= 1
    error_message = "At least one private subnet is required."
  }
}

variable "alb_security_group_id" {
  description = "Security group for the load balancer."
  type        = string
}

variable "app_security_group_id" {
  description = "Security group for the app instances."
  type        = string
}

variable "instance_profile_name" {
  description = "IAM instance profile attached to app instances."
  type        = string
}

# ---------- Load balancer ----------

variable "app_port" {
  description = "Port the app listens on inside each instance."
  type        = number
  default     = 8000

  validation {
    condition     = var.app_port >= 1024 && var.app_port <= 65535
    error_message = "app_port must be between 1024 and 65535."
  }
}

variable "health_check_path" {
  description = "Path the load balancer probes on each instance. /ready also checks the database connection."
  type        = string
  default     = "/ready"

  validation {
    condition     = startswith(var.health_check_path, "/")
    error_message = "health_check_path must start with \"/\"."
  }
}

variable "access_logs_bucket" {
  description = "S3 bucket that receives load balancer access logs (from the monitoring module)."
  type        = string
}

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS. null = HTTP only (no domain in this demo); when set, HTTP is redirected to HTTPS."
  type        = string
  default     = null

  validation {
    condition     = var.certificate_arn == null || can(regex("^arn:aws[a-zA-Z-]*:acm:", var.certificate_arn))
    error_message = "certificate_arn must be null or an ACM certificate ARN."
  }
}

variable "enable_deletion_protection" {
  description = "Protect the load balancer from accidental deletion."
  type        = bool
  default     = true
}

# ---------- Instances ----------

variable "instance_type" {
  description = "EC2 instance type for app instances. x86_64 families only, to match the Amazon Linux 2023 x86_64 AMI."
  type        = string
  default     = "t3.micro"

  validation {
    condition     = can(regex("^(t3|t3a|m6i|m7i|c6i|c7i)\\.[a-z0-9]+$", var.instance_type))
    error_message = "instance_type must be an x86_64 type from the t3, t3a, m6i, m7i, c6i or c7i families."
  }
}

variable "ami_ssm_parameter" {
  description = "Public SSM parameter that resolves to the latest Amazon Linux 2023 AMI, so no AMI ID is hardcoded."
  type        = string
  default     = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

variable "root_volume_size_gb" {
  description = "Size of the encrypted root volume in GiB."
  type        = number
  default     = 20

  validation {
    condition     = var.root_volume_size_gb >= 8 && var.root_volume_size_gb <= 100
    error_message = "root_volume_size_gb must be between 8 and 100."
  }
}

variable "app_container_image" {
  description = "Container image of the Task API to run on each instance. Empty = instances boot without the app (demo only)."
  type        = string
  default     = ""
}

variable "app_log_group_name" {
  description = "CloudWatch log group the app container writes to (from the monitoring module)."
  type        = string
}

# ---------- Auto Scaling (mirrors the local M7 controller) ----------

variable "asg_min_size" {
  description = "Minimum number of app instances (M7 local minimum: 1)."
  type        = number
  default     = 1

  validation {
    condition     = var.asg_min_size >= 1
    error_message = "asg_min_size must be at least 1."
  }
}

variable "asg_max_size" {
  description = "Maximum number of app instances (M7 local maximum: 3)."
  type        = number
  default     = 3

  validation {
    condition     = var.asg_max_size >= var.asg_min_size && var.asg_max_size <= 10
    error_message = "asg_max_size must be >= asg_min_size and at most 10."
  }
}

variable "asg_desired_capacity" {
  description = "Initial number of app instances. Afterwards the scaling policy manages it."
  type        = number
  default     = 1

  validation {
    condition     = var.asg_desired_capacity >= var.asg_min_size && var.asg_desired_capacity <= var.asg_max_size
    error_message = "asg_desired_capacity must be between asg_min_size and asg_max_size."
  }
}

variable "target_requests_per_instance" {
  description = "Target-tracking goal: requests per instance per minute (ALBRequestCountPerTarget). 600/min = the M7 scale-out threshold of 10 req/s per replica."
  type        = number
  default     = 600

  validation {
    condition     = var.target_requests_per_instance >= 10
    error_message = "target_requests_per_instance must be at least 10."
  }
}

variable "health_check_grace_period_seconds" {
  description = "Seconds after launch before failed health checks can replace an instance (boot time + app start)."
  type        = number
  default     = 180

  validation {
    condition     = var.health_check_grace_period_seconds >= 60 && var.health_check_grace_period_seconds <= 900
    error_message = "health_check_grace_period_seconds must be between 60 and 900."
  }
}

variable "tags" {
  description = "Extra tags for resources in this module. Also propagated to instances launched by the Auto Scaling group."
  type        = map(string)
  default     = {}
}
