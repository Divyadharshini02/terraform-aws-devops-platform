# Terraform — AWS design (validation only)

This folder describes how the locally running platform (NGINX, FastAPI replicas,
PostgreSQL, Prometheus/Grafana, the M7 autoscaler) would look on AWS.

> **It is never deployed.** It is only formatted, validated and statically scanned.
> `terraform plan`, `apply` and `destroy` are not used in this project, and the
> code contains no credentials, account IDs or passwords.

## Layout

```text
terraform/
├── modules/
│   ├── networking/   VPC, public + private subnets in 2 AZs, routing, optional NAT, flow logs
│   ├── security/     security groups (ALB -> app -> DB), EC2 IAM role (SSM, CloudWatch agent)
│   ├── compute/      Application Load Balancer, launch template, Auto Scaling group, scaling policy
│   ├── database/     RDS PostgreSQL (encrypted, private, AWS-managed password)
│   └── monitoring/   S3 bucket for ALB logs, app log group, SNS topic, CloudWatch alarms
├── environments/
│   └── dev/          root configuration that wires the modules together
└── .tflint.hcl       TFLint rules
```

## Local ↔ AWS mapping

| Local (M3–M7) | AWS (this code) |
|---|---|
| Docker network | VPC with public and private subnets |
| NGINX on :8081 | Application Load Balancer |
| App replicas (`docker compose --scale`) | EC2 instances in an Auto Scaling group |
| M7 controller: scale out above 10 req/s per replica | Target tracking on `ALBRequestCountPerTarget` = 600 req/min |
| Postgres container + `pgdata` volume | RDS PostgreSQL with automated backups |
| Prometheus + Grafana | CloudWatch metrics, log groups and alarms |
| NGINX access log | ALB access logs in S3 |

## Validate locally (Docker, no Terraform install, no AWS credentials)

Run from the repository root in PowerShell. Only the `terraform/` folder is mounted;
no AWS credentials are passed into the containers.

```powershell
$tf = "hashicorp/terraform:1.16.6"
docker run --rm -v "${PWD}\terraform:/work" -w /work $tf fmt -check -recursive
docker run --rm -v "${PWD}\terraform:/work" -w /work/environments/dev $tf init -backend=false
docker run --rm -v "${PWD}\terraform:/work" -w /work/environments/dev $tf validate
```

`init -backend=false` downloads the AWS provider plugin into
`environments/dev/.terraform/` (git-ignored) and records its exact version in
`.terraform.lock.hcl` (committed).

## Cost-aware defaults (if this were ever deployed)

- No NAT gateway (`enable_nat_gateway = false`), single-AZ database (`multi_az = false`).
- Small instance classes: `t3.micro`, `db.t4g.micro`.
- Log retention and S3 lifecycle expiry limit storage growth.
