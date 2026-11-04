terraform {
  # Validated with the pinned Docker image hashicorp/terraform:1.16.6.
  required_version = ">= 1.16.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0" # exact version is recorded in .terraform.lock.hcl
    }
  }

  # No backend block on purpose: this project is validated locally only
  # (terraform init -backend=false) and is never applied.
}
