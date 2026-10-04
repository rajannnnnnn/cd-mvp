# A single demo server on AWS: Ubuntu 24.04, 2 vCPU / 4 GB, which installs and starts the app by itself on first boot.
# Everything is created in your default VPC. `terraform destroy` removes all of it and stops the billing.
terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = var.region
}

# The current Ubuntu 24.04 LTS image published by Canonical (looked up, so no AMI id to maintain)
data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

data "aws_vpc" "default" {
  default = true
}

resource "aws_security_group" "demo" {
  name_prefix = "saathi-demo-"
  description = "Saathi demo: web (80/443) and optional SSH"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.allowed_ips
  }
  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_ips
  }
  dynamic "ingress" {
    for_each = var.ssh_key_name == "" ? [] : [1]
    content {
      description = "SSH"
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = var.allowed_ips
    }
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  lifecycle { create_before_destroy = true }
}

locals {
  # The app also checks the caller's address itself (a second lock), unless everyone is allowed
  everyone  = contains(var.allowed_ips, "0.0.0.0/0")
  allow_ips = local.everyone ? "0.0.0.0/0 ::/0" : join(" ", var.allowed_ips)
}

resource "aws_instance" "demo" {
  ami                    = nonsensitive(data.aws_ssm_parameter.ubuntu.value)
  instance_type          = var.instance_type
  key_name               = var.ssh_key_name == "" ? null : var.ssh_key_name
  vpc_security_group_ids = [aws_security_group.demo.id]

  root_block_device {
    volume_size = 40
    volume_type = "gp3"
  }

  # First-boot script: swap (the image build needs memory), then the project's one-command server setup. Its log: /var/log/saathi-setup.log
  user_data = <<-EOT
    #!/bin/bash
    set -x
    exec > /var/log/saathi-setup.log 2>&1
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
    export ALLOW_IPS="${local.allow_ips}"
    export BRANCH="${var.branch}"
    ${var.domain == "" ? "" : "export DOMAIN=\"${var.domain}\""}
    curl -fsSL https://raw.githubusercontent.com/rajannnnnnn/cd-mvp/${var.branch}/scripts/server-setup.sh | bash
    echo SETUP-FINISHED
  EOT

  tags = { Name = "saathi-demo" }
}
