# Security: one security group per tier, each allowing only the traffic the
# next tier needs (internet -> ALB -> app -> database), plus the EC2 IAM role.
# Same idea as the local setup, where only NGINX is published and Postgres is
# reachable only on the Docker network.

data "aws_partition" "current" {}

# ---------- Load balancer ----------

resource "aws_security_group" "alb" {
  #checkov:skip=CKV2_AWS_5:False positive. Attached to the load balancer in modules/compute (aws_lb.this security_groups) via module output.
  name_prefix = "${var.name}-alb-"
  description = "Load balancer: HTTP/HTTPS in from allowed CIDRs, out only to the app"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${var.name}-alb-sg" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  #checkov:skip=CKV_AWS_260:Public web entry point on the load balancer only. Restrict with allowed_ingress_cidrs for private demos.
  for_each = toset(var.allowed_ingress_cidrs)

  security_group_id = aws_security_group.alb.id
  description       = "HTTP from allowed client range"
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  for_each = var.enable_https ? toset(var.allowed_ingress_cidrs) : toset([])

  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from allowed client range"
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "alb_to_app" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Forward requests to app instances only"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = var.app_port
  to_port                      = var.app_port
}

# ---------- App instances ----------

resource "aws_security_group" "app" {
  #checkov:skip=CKV2_AWS_5:False positive. Attached to app instances in modules/compute (aws_launch_template.app vpc_security_group_ids) via module output.
  name_prefix = "${var.name}-app-"
  description = "App instances: app port only from the ALB; no SSH (use SSM)"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${var.name}-app-sg" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  security_group_id            = aws_security_group.app.id
  description                  = "App port from the load balancer only"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = var.app_port
  to_port                      = var.app_port
}

resource "aws_vpc_security_group_egress_rule" "app_to_db" {
  security_group_id            = aws_security_group.app.id
  description                  = "PostgreSQL to the database only"
  referenced_security_group_id = aws_security_group.db.id
  ip_protocol                  = "tcp"
  from_port                    = var.db_port
  to_port                      = var.db_port
}

# HTTPS out is needed for SSM, CloudWatch, package and container image downloads.
resource "aws_vpc_security_group_egress_rule" "app_https_out" {
  security_group_id = aws_security_group.app.id
  description       = "HTTPS to AWS APIs and package registries"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# ---------- Database ----------

# No egress rules: the database never needs to start outbound connections.
resource "aws_security_group" "db" {
  #checkov:skip=CKV2_AWS_5:False positive. Attached to the RDS instance in modules/database (aws_db_instance.this vpc_security_group_ids) via module output.
  name_prefix = "${var.name}-db-"
  description = "PostgreSQL: only from app instances; no outbound rules"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${var.name}-db-sg" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "db_from_app" {
  security_group_id            = aws_security_group.db.id
  description                  = "PostgreSQL from app instances only"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = var.db_port
  to_port                      = var.db_port
}

# ---------- EC2 instance role ----------

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app" {
  name_prefix        = "${var.name}-app-"
  description        = "Role for app instances: SSM access and CloudWatch agent only"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json

  tags = var.tags
}

# AWS-managed policies, referenced by partition so no account ID is hardcoded.
resource "aws_iam_role_policy_attachment" "app" {
  for_each = toset([
    "AmazonSSMManagedInstanceCore", # shell access via Session Manager instead of SSH
    "CloudWatchAgentServerPolicy",  # ship app logs and metrics to CloudWatch
  ])

  role       = aws_iam_role.app.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/${each.value}"
}

resource "aws_iam_instance_profile" "app" {
  name_prefix = "${var.name}-app-"
  role        = aws_iam_role.app.name

  tags = var.tags
}
