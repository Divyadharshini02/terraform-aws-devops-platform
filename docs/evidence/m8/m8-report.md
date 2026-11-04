# M8 — Terraform AWS modules (validation only)

Date: 2026-11-04. **No AWS resources were created.** The configuration was formatted,
validated and statically scanned inside Docker containers that had no access to AWS
credentials. `terraform plan`, `apply` and `destroy` were never run.

## Evidence files

| File | Content |
|---|---|
| [01-fmt-check.txt](01-fmt-check.txt) | `terraform fmt -check -recursive -diff` (includes the one-time image pull) |
| [02-init.txt](02-init.txt) | `terraform init -backend=false` |
| [03-validate.txt](03-validate.txt) | `terraform validate` |
| [04-tflint.txt](04-tflint.txt) | `tflint --init`, `--version`, recursive lint (text + JSON) |
| [05-checkov-run.txt](05-checkov-run.txt) | Checkov command, summary, failed-check list and exit code (first run). Trimmed from the 768 KB console capture, whose CLI and JSON parts duplicated the two files below. |
| [checkov/results_cli.txt](checkov/results_cli.txt), [checkov/results_json.json](checkov/results_json.json) | Full Checkov results, **first run** (173 passed / 21 failed) |
| [06-fmt-check-final.txt](06-fmt-check-final.txt) | `terraform fmt -check` after the cleanup |
| [07-validate-final.txt](07-validate-final.txt) | `terraform validate` after the cleanup |
| [08-tflint-final.txt](08-tflint-final.txt) | TFLint after the cleanup (JSON) |
| [09-checkov-final-run.txt](09-checkov-final-run.txt) | Checkov command and exit code after the cleanup |
| [checkov-final/results_cli.txt](checkov-final/results_cli.txt), [checkov-final/results_json.json](checkov-final/results_json.json) | Full Checkov results, **final run** (174 passed / 0 failed / 20 skipped) |

## Architecture

```
Internet ─► ALB (public subnets, 2 AZs) ─► EC2 app instances, Auto Scaling 1–3 (private subnets) ─► RDS PostgreSQL (private)
                │ access logs ─► S3                      │ logs ─► CloudWatch            alarms ─► SNS
```

Root configuration: `terraform/environments/dev/` (no backend, credential-free provider block,
`default_tags`). Five modules under `terraform/modules/`:

| Module | Resources | Local counterpart |
|---|---|---|
| networking | VPC, 2 public + 2 private subnets, IGW, route tables, optional NAT (off), locked default SG, VPC flow logs → CloudWatch (least-privilege IAM role) | Docker network |
| security | ALB / app / DB security groups chained by SG reference; EC2 role + instance profile (SSM, CloudWatch agent) | Only NGINX published; Postgres internal |
| compute | ALB (drop invalid headers, access logs, deletion protection), target group (health check `/ready`), HTTP listener (HTTPS + redirect when a certificate is given), launch template (AL2023 via SSM parameter, IMDSv2, encrypted gp3), Auto Scaling group (ELB health checks), target tracking on `ALBRequestCountPerTarget` = 600/min | NGINX + app replicas + **M7 controller** (10 req/s per replica = 600 req/min per instance) |
| database | DB subnet group, parameter group (`rds.force_ssl=1`, `log_connections=1`), RDS PostgreSQL 16 (encrypted, private, AWS-managed master password, 7-day backups, IAM auth, log exports, deletion protection) | Postgres container + `pgdata` |
| monitoring | S3 bucket for ALB logs (public access blocked, ACLs disabled, versioned, SSE-S3, lifecycle, TLS-only + log-delivery policy), app log group, SNS topic (AWS-managed KMS), 6 alarms: target 5xx, ELB 5xx, unhealthy targets, p95 latency > 0.5 s, DB CPU, DB free storage | Prometheus + Grafana, NGINX access log |

Design notes:
- Variables have descriptions, types, defaults and validation rules (CIDR format, name length,
  instance families, AZ count, retention values, `min ≤ desired ≤ max` replicas, e-mail format).
- No credentials, passwords, account IDs or provisioners. Account-specific values come from data
  sources (`aws_caller_identity`, `aws_partition`, `aws_elb_service_account`, SSM AMI parameter).
- Cost-aware defaults: no NAT gateway, single-AZ RDS, `t3.micro` / `db.t4g.micro`, log retention
  and S3 expiry.
- monitoring ↔ compute reference each other (bucket → ALB → alarms); Terraform resolves this per
  resource. The bucket output is read from the bucket *policy*, so the ALB waits until log
  delivery is permitted.

## Tool versions

| Tool | Version | Image (unpacked) |
|---|---|---|
| Terraform | 1.16.6 (`hashicorp/terraform:1.16.6`) | 198 MB |
| AWS provider | **6.71.0** (signed by HashiCorp), constraint `~> 6.0`, pinned in `.terraform.lock.hcl` | 761 MB plugin in `.terraform/` (git-ignored) |
| TFLint | 0.64.0, ruleset aws 0.49.0, terraform 0.15.0 (bundled) | 124 MB |
| Checkov | 3.3.29 (`bridgecrew/checkov:3.3.29`, 188 MB compressed) | 819 MB |

## Commands and results

All run from the repository root with only `terraform/` mounted
(Checkov: read-only mount); no `-e`/env-file, no `~/.aws` mount.

| # | Command | Result |
|---|---|---|
| 1 | `terraform fmt -check -recursive -diff` | ✅ exit 0, no files needed formatting |
| 2 | `terraform init -backend=false -input=false` | ✅ exit 0, 5 modules, `hashicorp/aws v6.71.0`, lock file created |
| 3 | `terraform validate` | ✅ `Success! The configuration is valid.` — no warnings, no cycles |
| 4 | `tflint --recursive` (recommended preset + AWS ruleset, `deep_check = false`) | ✅ `{"issues":[],"errors":[]}` |
| 5 | `checkov -d /work --framework terraform --skip-download` | ⚠️ 173 passed, **21 failed**, 0 parsing errors, 57 resources (exit 1 = failures present) |

`--skip-download` prevents Checkov from contacting the Prisma Cloud platform; no API key was used.

### Cleanup and final run

After the first run, two approved changes were made:
1. **Fix:** launch template `http_put_response_hop_limit` 2 → **1**, and the incorrect comment replaced
   (`modules/compute/main.tf`).
2. **Inline skips:** 20 `#checkov:skip=<ID>:<reason>` comments — one per accepted finding (17) and
   verified false positive (3) — placed inside the flagged resources. No other check is suppressed.

`terraform init` was not re-run: no provider version, module source or backend changed.

| # | Command | Result |
|---|---|---|
| 6 | `terraform fmt -check -recursive -diff` | ✅ exit 0 |
| 7 | `terraform validate` | ✅ `Success! The configuration is valid.` |
| 8 | `tflint --recursive --format json` | ✅ `{"issues":[],"errors":[]}` |
| 9 | `checkov` (same command as #5) | ✅ **exit 0** — **174 passed, 0 failed, 20 skipped**, 0 parsing errors, 57 resources |

| Checkov | First run | Final run |
|---|---|---|
| Passed | 173 | **174** (+ CKV_AWS_341 on `module.compute.aws_launch_template.app`) |
| Failed | 21 | **0** |
| Skipped | 0 | **20**, each with its reason reported by Checkov (`suppress_comment`) |
| Total checks | 194 | 194 |
| Exit code | 1 | **0** — usable as a pass/fail CI gate (M9) |

## Checkov findings and disposition

| Check | Resource | Disposition | Reason |
|---|---|---|---|
| CKV_AWS_341 IMDS hop limit > 1 | launch template | **Fixed → passes** | Hop limit was 2 with an incorrect justification. The only AWS caller on the instance is Docker's `awslogs` driver, which runs in the host daemon, so it is now 1: a compromised app container cannot read the instance role's credentials. |
| CKV2_AWS_5 SG not attached (×3) | alb / app / db SGs | **False positive** | Attached via module outputs: `compute/main.tf:18` (ALB), `:107` (launch template), `database/main.tf:54` (RDS). Checkov does not follow the cross-module reference. |
| CKV_AWS_2, CKV_AWS_103, CKV2_AWS_20 | HTTP listener / ALB | Accepted (by design) | No domain or ACM certificate in this demo. Setting `certificate_arn` switches to HTTPS (TLS 1.3 policy) and redirects HTTP → HTTPS. |
| CKV_AWS_378 HTTP target group | target group | Accepted | TLS terminates at the ALB; ALB → instance traffic stays inside the VPC on a SG-restricted port. |
| CKV_AWS_260 0.0.0.0/0 → port 80 | ALB ingress rule | Accepted | Public web entry point; `allowed_ingress_cidrs` restricts it when needed. |
| CKV2_AWS_28 WAF | ALB | Accepted (cost) | WAF has a monthly charge; recommended for production. |
| CKV_AWS_157 Multi-AZ | RDS | Accepted (cost) | `db_multi_az` variable enables it. |
| CKV_AWS_118 enhanced monitoring | RDS | Accepted (cost/complexity) | Needs an extra IAM role and CloudWatch ingestion; basic RDS metrics feed the alarms. |
| CKV_AWS_353 Performance Insights | RDS | Accepted | Not needed for a demo; instance-class support for `db.t4g.micro` was not verified. |
| CKV_AWS_158 KMS for log groups (×2) | app + flow-log groups | Accepted (cost) | Log groups are encrypted by default with AWS-owned keys; a customer-managed key has a monthly charge. |
| CKV_AWS_338 log retention ≥ 1 year | flow-log group | Accepted (cost) | 30 days for a dev environment; variable-controlled. |
| CKV_AWS_145 S3 KMS encryption | ALB log bucket | Accepted (AWS limitation) | ALB access logs support only SSE-S3 (AES256), which is enabled. |
| CKV_AWS_144 cross-region replication | ALB log bucket | Accepted | Not justified for short-lived access logs. |
| CKV_AWS_18 bucket access logging | ALB log bucket | Accepted | Would require another log bucket for the log bucket. |
| CKV2_AWS_62 event notifications | ALB log bucket | Accepted | No consumer for log-arrival events. |
| CKV_AWS_394 pin AZ identity | `aws_availability_zones` | Accepted (low risk) | The first `az_count` AZs are used via `slice()`; pinning zone IDs would add a region-specific variable. |

Summary: **1 fixed, 3 false positives (one rule), 17 accepted.** Every accepted finding and false
positive now carries an inline `#checkov:skip=<ID>:<reason>` comment in the resource it applies to,
with the same reason as in this table, so the justification lives next to the code and Checkov
reports it as *skipped with reason* rather than hiding it.

## Safety verification

- **No AWS resources created:** only `fmt`, `init -backend=false`, `validate`, TFLint and Checkov ran;
  no `.tfstate` or `.tfplan` file exists under `terraform/`; no backend is configured.
- **No credentials:** provider block has only `region` and `default_tags`; containers saw only the
  `terraform/` folder (and a plugin cache / output folder), no `~/.aws` and no environment variables;
  scans found 0 account IDs, 0 key/password assignments and 0 `.env` values in `terraform/`.
- **M1–M7 unaffected:** only `.gitignore` was modified among existing files (10 Terraform patterns
  appended); autoscaler code, `docker-compose.yml` and M6/M7 evidence are unchanged.
- **No commits or pushes.**

## Limitations and trade-offs

- Never deployed, so runtime behaviour (boot script, health checks, scaling) is not proven on AWS.
  `terraform validate` checks configuration correctness, not whether AWS would accept every value.
- The boot script runs the app container only if `app_container_image` is set; there is no image
  registry or database-connection wiring (the app would fall back to SQLite).
- Without a NAT gateway, private instances cannot download packages or images; a real deployment
  needs NAT or VPC endpoints (both have hourly cost).
- Single NAT gateway (when enabled) and single-AZ RDS are cost trade-offs, not production patterns.
- No remote state backend or state locking, by design for a local-only project.
- Checkov results depend on the scanner version; cross-module attachments are reported as false
  positives.

## Interview explanation

> "I described the local platform as modular Terraform for AWS: networking, security, compute,
> database and monitoring modules, wired together in a dev environment. It mirrors what runs
> locally — the ALB is NGINX, the Auto Scaling group is my replica scaling, and the target-tracking
> policy uses the same requests-per-instance idea as my M7 controller. Security is built in:
> least-privilege security groups chained by reference, no SSH, IMDSv2, encryption everywhere,
> and an AWS-managed database password so no secret is in code or state. Because the project has a
> zero-spend rule, I validate it without deploying: fmt, init without a backend, validate, TFLint and
> Checkov, all in pinned Docker images that have no access to AWS credentials. Checkov found 21
> issues; I triaged every one: I fixed the real weakness (an IMDS hop limit I had set too high),
> verified one rule as a false positive, and documented the rest as cost or design trade-offs with
> inline skip reasons. The final scan is 174 passed, 0 failed, 20 skipped with reasons, and exits
> 0, so it can gate CI."
