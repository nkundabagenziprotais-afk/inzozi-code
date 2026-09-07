variable "name" {
  description = "Disposable staging server name."
  type        = string
  default     = "inzozi-code-staging"
}

variable "location" {
  description = "Hetzner Cloud location. Nuremberg is the default Germany staging region."
  type        = string
  default     = "nbg1"
}

variable "server_type" {
  description = "Hetzner Cloud server type. CX33 is the alpha staging default."
  type        = string
  default     = "cx33"
}

variable "image" {
  description = "Base operating-system image."
  type        = string
  default     = "ubuntu-24.04"
}

variable "admin_user" {
  description = "Non-root SSH administrator created by cloud-init."
  type        = string
  default     = "inzozi"

  validation {
    condition     = can(regex("^[a-z_][a-z0-9_-]{0,30}$", var.admin_user))
    error_message = "admin_user must be a valid Linux username."
  }
}

variable "ssh_public_key" {
  description = "Operator SSH public key. Supply through TF_VAR_ssh_public_key; never commit a private key."
  type        = string

  validation {
    condition = (
      startswith(trimspace(var.ssh_public_key), "ssh-ed25519 ") ||
      startswith(trimspace(var.ssh_public_key), "ssh-rsa ") ||
      startswith(trimspace(var.ssh_public_key), "ecdsa-sha2-")
    )
    error_message = "ssh_public_key must be an OpenSSH public key."
  }
}

variable "ssh_source_cidrs" {
  description = "CIDR blocks allowed to reach SSH. Explicitly required to prevent accidental public SSH."
  type        = list(string)

  validation {
    condition     = length(var.ssh_source_cidrs) > 0 && alltrue([for cidr in var.ssh_source_cidrs : can(cidrhost(cidr, 0))])
    error_message = "ssh_source_cidrs must contain at least one valid IPv4/IPv6 CIDR."
  }

  validation {
    condition = alltrue([
      for cidr in var.ssh_source_cidrs : cidr != "0.0.0.0/0" && cidr != "::/0"
    ])
    error_message = "SSH may not be opened to the entire internet. Provide the operator's current CIDR instead."
  }
}

variable "public_web_cidrs" {
  description = "CIDRs allowed to reach HTTP/HTTPS/ICMP."
  type        = list(string)
  default     = ["0.0.0.0/0", "::/0"]
}

variable "workspace_quota_volume_size_gb" {
  description = "Physical XFS staging pool for hard per-workspace project quotas. This is separate from each workspace's byte limit."
  type        = number
  default     = 30

  validation {
    condition     = var.workspace_quota_volume_size_gb >= 20 && var.workspace_quota_volume_size_gb <= 1024 && floor(var.workspace_quota_volume_size_gb) == var.workspace_quota_volume_size_gb
    error_message = "workspace_quota_volume_size_gb must be a whole number between 20 and 1024 GB."
  }
}

variable "labels" {
  description = "Additional non-secret Hetzner resource labels."
  type        = map(string)
  default     = {}
}
