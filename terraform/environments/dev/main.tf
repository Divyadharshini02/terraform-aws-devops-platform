# dev environment: wires the five modules together.
#
#   internet -> ALB (public subnets) -> app instances in an Auto Scaling group
#   (private subnets) -> RDS PostgreSQL (private subnets)
#   + CloudWatch logs/alarms and an S3 bucket for ALB access logs
#
# Local counterparts: NGINX -> app replicas -> Postgres container, with
# Prometheus/Grafana for monitoring and the M7 controller for scaling.

locals {
  name = "${var.project_name}-${var.environment}"

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    Owner       = var.owner
    ManagedBy   = "terraform"
    Repository  = "terraform-aws-devops-platform"
  }
}

module "networking" {
  source = "../../modules/networking"

  name                    = local.name
  vpc_cidr                = var.vpc_cidr
  az_count                = var.az_count
  enable_nat_gateway      = var.enable_nat_gateway
  flow_log_retention_days = var.log_retention_days
}

module "security" {
  source = "../../modules/security"

  name                  = local.name
  vpc_id                = module.networking.vpc_id
  allowed_ingress_cidrs = var.allowed_ingress_cidrs
  enable_https          = var.certificate_arn != null
}

module "compute" {
  source = "../../modules/compute"

  name                  = local.name
  vpc_id                = module.networking.vpc_id
  public_subnet_ids     = module.networking.public_subnet_ids
  private_subnet_ids    = module.networking.private_subnet_ids
  alb_security_group_id = module.security.alb_security_group_id
  app_security_group_id = module.security.app_security_group_id
  instance_profile_name = module.security.app_instance_profile_name

  access_logs_bucket         = module.monitoring.alb_logs_bucket
  app_log_group_name         = module.monitoring.app_log_group_name
  certificate_arn            = var.certificate_arn
  enable_deletion_protection = var.alb_deletion_protection

  instance_type                = var.instance_type
  app_container_image          = var.app_container_image
  asg_min_size                 = var.asg_min_size
  asg_max_size                 = var.asg_max_size
  asg_desired_capacity         = var.asg_desired_capacity
  target_requests_per_instance = var.target_requests_per_instance

  # Provider default_tags do not reach instances launched by an Auto Scaling
  # group, so the compute module propagates these tags itself.
  tags = local.common_tags
}

module "database" {
  source = "../../modules/database"

  name                 = local.name
  private_subnet_ids   = module.networking.private_subnet_ids
  db_security_group_id = module.security.db_security_group_id
  instance_class       = var.db_instance_class
  multi_az             = var.db_multi_az
  deletion_protection  = var.db_deletion_protection
}

# The monitoring module both provides the log bucket to compute and watches
# compute/database through alarms. Terraform orders this per resource:
# bucket -> load balancer -> alarms, so there is no dependency cycle.
module "monitoring" {
  source = "../../modules/monitoring"

  name                    = local.name
  log_retention_days      = var.log_retention_days
  alarm_email             = var.alarm_email
  alb_arn_suffix          = module.compute.alb_arn_suffix
  target_group_arn_suffix = module.compute.target_group_arn_suffix
  db_instance_identifier  = module.database.db_instance_identifier
}
