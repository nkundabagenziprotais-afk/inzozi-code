terraform {
  required_version = ">= 1.16.0, < 1.17.0"

  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "1.68.0"
    }
  }
}

provider "hcloud" {
  # Authentication is intentionally environment-only via HCLOUD_TOKEN.
}

locals {
  common_labels = merge(
    {
      application = "inzozi-code"
      environment = "staging"
      lifecycle   = "ephemeral"
      managed_by  = "terraform"
    },
    var.labels,
  )
}

resource "hcloud_ssh_key" "staging" {
  name       = "${var.name}-bootstrap"
  public_key = trimspace(var.ssh_public_key)
  labels     = local.common_labels
}

resource "hcloud_firewall" "staging" {
  name   = "${var.name}-firewall"
  labels = local.common_labels

  rule {
    direction   = "in"
    protocol    = "tcp"
    port        = "22"
    source_ips  = var.ssh_source_cidrs
    description = "SSH only from explicitly approved operator networks"
  }

  rule {
    direction   = "in"
    protocol    = "tcp"
    port        = "80"
    source_ips  = var.public_web_cidrs
    description = "HTTP for staging and ACME challenge"
  }

  rule {
    direction   = "in"
    protocol    = "tcp"
    port        = "443"
    source_ips  = var.public_web_cidrs
    description = "HTTPS staging access"
  }

  rule {
    direction   = "in"
    protocol    = "icmp"
    source_ips  = var.public_web_cidrs
    description = "ICMP for basic network diagnostics"
  }
}

resource "hcloud_server" "staging" {
  name        = var.name
  image       = var.image
  server_type = var.server_type
  location    = var.location

  ssh_keys     = [hcloud_ssh_key.staging.id]
  firewall_ids = [hcloud_firewall.staging.id]
  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    admin_user     = var.admin_user
    ssh_public_key = trimspace(var.ssh_public_key)
  })

  backups                  = false
  delete_protection        = false
  rebuild_protection       = false
  shutdown_before_deletion = true
  labels                   = local.common_labels

  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }
}

resource "hcloud_volume" "workspace_quota" {
  name      = "${var.name}-workspace-quota"
  size      = var.workspace_quota_volume_size_gb
  server_id = hcloud_server.staging.id
  automount = false
  format    = "xfs"
  labels = merge(local.common_labels, {
    purpose = "workspace-quota"
  })
}
