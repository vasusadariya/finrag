output "public_ip" {
  description = "Point the domain's A record here (DuckDNS: update the subdomain with this IP)."
  value       = oci_core_instance.app.public_ip
}

output "url" {
  value = "https://${var.domain}"
}

output "ssh" {
  value = "ssh ubuntu@${oci_core_instance.app.public_ip}"
}
