variable "name" {
  description = "Name prefix for security resources, e.g. \"taskapi-dev\"."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name))
    error_message = "name must be 3-31 characters: lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "vpc_id" {
  description = "ID of the VPC the security groups belong to."
  type        = string
}

variable "allowed_ingress_cidrs" {
  description = "IPv4 CIDR blocks allowed to reach the load balancer on HTTP/HTTPS. A public website uses 0.0.0.0/0; restrict it for private demos."
  type        = list(string)
  default     = ["0.0.0.0/0"]

  validation {
    condition     = length(var.allowed_ingress_cidrs) > 0 && alltrue([for c in var.allowed_ingress_cidrs : can(cidrhost(c, 0))])
    error_message = "allowed_ingress_cidrs must be a non-empty list of valid IPv4 CIDR blocks."
  }
}

variable "app_port" {
  description = "Port the FastAPI app listens on inside each instance (matches the local container port 8000)."
  type        = number
  default     = 8000

  validation {
    condition     = var.app_port >= 1024 && var.app_port <= 65535
    error_message = "app_port must be an unprivileged port between 1024 and 65535."
  }
}

variable "db_port" {
  description = "PostgreSQL port."
  type        = number
  default     = 5432

  validation {
    condition     = var.db_port >= 1024 && var.db_port <= 65535
    error_message = "db_port must be between 1024 and 65535."
  }
}

variable "enable_https" {
  description = "Whether the load balancer also accepts HTTPS (443). Requires a certificate in the compute module."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Extra tags for resources in this module."
  type        = map(string)
  default     = {}
}
