variable "name" {
  description = "Name prefix for all networking resources, e.g. \"taskapi-dev\"."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name))
    error_message = "name must be 3-31 characters: lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "vpc_cidr" {
  description = "IPv4 CIDR block of the VPC. A /16 leaves room for /24 subnets."
  type        = string
  default     = "10.20.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0)) && tonumber(split("/", var.vpc_cidr)[1]) <= 20
    error_message = "vpc_cidr must be a valid IPv4 CIDR block of size /20 or larger (e.g. 10.20.0.0/16)."
  }
}

variable "az_count" {
  description = "Number of availability zones to spread subnets across (2 gives high availability for the ALB and RDS subnet group)."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3 (ALB and RDS subnet groups need at least 2 AZs)."
  }
}

variable "enable_nat_gateway" {
  description = "Create a single NAT gateway so private instances can reach the internet. Off by default because a NAT gateway is billed per hour."
  type        = bool
  default     = false
}

variable "flow_log_retention_days" {
  description = "CloudWatch retention for VPC flow logs, in days."
  type        = number
  default     = 30

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365], var.flow_log_retention_days)
    error_message = "flow_log_retention_days must be a CloudWatch-supported retention value (1-365 days)."
  }
}

variable "tags" {
  description = "Extra tags for resources in this module (provider default_tags are applied as well)."
  type        = map(string)
  default     = {}
}
