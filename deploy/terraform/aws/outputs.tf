output "public_ip" {
  value = aws_instance.demo.public_ip
}

output "url" {
  description = "Open this after about 10-15 minutes (the first build is slow)."
  value       = var.domain == "" ? "http://${aws_instance.demo.public_ip}" : "https://${var.domain}"
}

output "watch_the_setup" {
  description = "Only works if you gave ssh_key_name."
  value       = "ssh ubuntu@${aws_instance.demo.public_ip} 'sudo tail -f /var/log/saathi-setup.log'"
}
