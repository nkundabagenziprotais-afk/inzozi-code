output "server_id" {
  description = "Hetzner Cloud server ID."
  value       = hcloud_server.staging.id
}

output "server_name" {
  description = "Disposable staging server name."
  value       = hcloud_server.staging.name
}

output "ipv4_address" {
  description = "Public IPv4 address for temporary staging DNS and SSH."
  value       = hcloud_server.staging.ipv4_address
}

output "ipv6_address" {
  description = "Public IPv6 address assigned to the staging server."
  value       = hcloud_server.staging.ipv6_address
}

output "ssh_command" {
  description = "Convenience SSH command. The private key remains operator-owned and is never stored in Terraform."
  value       = "ssh ${var.admin_user}@${hcloud_server.staging.ipv4_address}"
}

output "staging_url" {
  description = "Canonical staging hostname once DNS points at this server."
  value       = "https://code-staging.inzozidigital.com"
}
