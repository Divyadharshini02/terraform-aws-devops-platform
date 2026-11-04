# No credentials here, ever. "terraform validate" never contacts AWS, so it
# needs none. In a real deployment credentials would come from the
# environment (e.g. a CI role via OIDC), never from code.
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}
