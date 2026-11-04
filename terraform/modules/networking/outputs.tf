output "vpc_id" {
  description = "ID of the VPC."
  value       = aws_vpc.this.id
}

output "vpc_cidr" {
  description = "CIDR block of the VPC."
  value       = aws_vpc.this.cidr_block
}

output "public_subnet_ids" {
  description = "IDs of the public subnets (load balancer)."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "IDs of the private subnets (app instances and database)."
  value       = aws_subnet.private[*].id
}

output "availability_zones" {
  description = "Availability zones used by the subnets."
  value       = local.azs
}

output "nat_gateway_enabled" {
  description = "Whether a NAT gateway was created for private subnet egress."
  value       = var.enable_nat_gateway
}
