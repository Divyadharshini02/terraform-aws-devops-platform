# Compute: an Application Load Balancer in the public subnets (the AWS
# counterpart of NGINX) and an Auto Scaling group of app instances in the
# private subnets, scaled by requests per instance (the AWS counterpart of the
# M7 controller's "requests per replica" rule).

data "aws_region" "current" {}

data "aws_ssm_parameter" "ami" {
  name = var.ami_ssm_parameter
}

# ---------- Load balancer ----------

resource "aws_lb" "this" {
  #checkov:skip=CKV2_AWS_20:No domain or ACM certificate in this demo. When certificate_arn is set the HTTP listener redirects to HTTPS.
  #checkov:skip=CKV2_AWS_28:WAF has a monthly charge and this design is never deployed. Recommended for production.
  name                       = substr("${var.name}-alb", 0, 32)
  load_balancer_type         = "application"
  internal                   = false
  security_groups            = [var.alb_security_group_id]
  subnets                    = var.public_subnet_ids
  drop_invalid_header_fields = true
  enable_deletion_protection = var.enable_deletion_protection

  access_logs {
    bucket  = var.access_logs_bucket
    enabled = true
  }

  tags = merge(var.tags, { Name = "${var.name}-alb" })
}

resource "aws_lb_target_group" "app" {
  #checkov:skip=CKV_AWS_378:TLS terminates at the ALB. ALB to instance traffic stays inside the VPC on a port restricted by security group.
  name                 = substr("${var.name}-app-tg", 0, 32)
  port                 = var.app_port
  protocol             = "HTTP"
  vpc_id               = var.vpc_id
  target_type          = "instance"
  deregistration_delay = 30 # seconds to drain in-flight requests on scale-in

  # The ALB probes over HTTP from outside the instance, so a slow probe process
  # inside the container (the M7 local healthcheck issue) does not apply here.
  health_check {
    path                = var.health_check_path
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = merge(var.tags, { Name = "${var.name}-app-tg" })
}

# HTTP listener: forwards to the app when no certificate is set; otherwise
# redirects every request to HTTPS.
resource "aws_lb_listener" "http" {
  #checkov:skip=CKV_AWS_2:HTTP only because there is no domain or ACM certificate. Setting certificate_arn turns this listener into an HTTPS redirect.
  #checkov:skip=CKV_AWS_103:TLS policy applies to the HTTPS listener, which uses ELBSecurityPolicy-TLS13-1-2-2021-06 when certificate_arn is set.
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  dynamic "default_action" {
    for_each = var.certificate_arn == null ? [1] : []
    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.app.arn
    }
  }

  dynamic "default_action" {
    for_each = var.certificate_arn == null ? [] : [1]
    content {
      type = "redirect"
      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }

  tags = var.tags
}

resource "aws_lb_listener" "https" {
  count = var.certificate_arn == null ? 0 : 1

  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }

  tags = var.tags
}

# ---------- Instances ----------

resource "aws_launch_template" "app" {
  name_prefix            = "${var.name}-app-"
  description            = "Task API instances"
  image_id               = data.aws_ssm_parameter.ami.insecure_value
  instance_type          = var.instance_type
  vpc_security_group_ids = [var.app_security_group_id]
  update_default_version = true

  user_data = base64encode(templatefile("${path.module}/user_data.sh.tftpl", {
    app_container_image = var.app_container_image
    app_port            = var.app_port
    region              = data.aws_region.current.region
    log_group_name      = var.app_log_group_name
  }))

  iam_instance_profile {
    name = var.instance_profile_name
  }

  # IMDSv2 only: blocks the credential-theft technique that uses IMDSv1.
  # Hop limit 1: only processes on the host itself can reach the metadata
  # service. The Docker daemon's awslogs driver runs on the host, so logging
  # still works, while the app container (one extra network hop) cannot read
  # the instance role's credentials if it is ever compromised.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  monitoring {
    enabled = true # 1-minute EC2 metrics (CPU, network) for troubleshooting
  }

  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      volume_size           = var.root_volume_size_gb
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags          = merge(var.tags, { Name = "${var.name}-app" })
  }

  tag_specifications {
    resource_type = "volume"
    tags          = merge(var.tags, { Name = "${var.name}-app-root" })
  }

  tags = var.tags
}

resource "aws_autoscaling_group" "app" {
  name_prefix               = "${var.name}-app-"
  vpc_zone_identifier       = var.private_subnet_ids
  min_size                  = var.asg_min_size
  max_size                  = var.asg_max_size
  desired_capacity          = var.asg_desired_capacity
  target_group_arns         = [aws_lb_target_group.app.arn]
  health_check_type         = "ELB" # replace instances that fail the ALB health check
  health_check_grace_period = var.health_check_grace_period_seconds
  default_instance_warmup   = var.health_check_grace_period_seconds

  launch_template {
    id      = aws_launch_template.app.id
    version = aws_launch_template.app.latest_version
  }

  instance_refresh {
    strategy = "Rolling"

    preferences {
      min_healthy_percentage = 50
    }
  }

  dynamic "tag" {
    for_each = merge(var.tags, { Name = "${var.name}-app" })
    content {
      key                 = tag.key
      value               = tag.value
      propagate_at_launch = true
    }
  }

  lifecycle {
    # The scaling policy changes desired capacity at runtime; do not reset it.
    ignore_changes = [desired_capacity]
  }
}

# Target tracking keeps requests per instance near the target, adding or
# removing instances automatically. AWS handles cooldown-like warm-up itself.
resource "aws_autoscaling_policy" "requests_per_instance" {
  name                   = "${var.name}-requests-per-instance"
  autoscaling_group_name = aws_autoscaling_group.app.name
  policy_type            = "TargetTrackingScaling"

  target_tracking_configuration {
    target_value = var.target_requests_per_instance

    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${aws_lb.this.arn_suffix}/${aws_lb_target_group.app.arn_suffix}"
    }
  }
}
