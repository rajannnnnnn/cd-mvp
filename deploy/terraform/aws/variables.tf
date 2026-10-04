variable "region" {
  description = "AWS region. ap-south-1 is Mumbai."
  type        = string
  default     = "ap-south-1"
}

variable "instance_type" {
  description = "t3.medium = 2 vCPU, 4 GB (about $0.04 an hour). Do not go below 4 GB of memory: the first build needs it."
  type        = string
  default     = "t3.medium"
}

variable "allowed_ips" {
  description = "Who may open the demo, as CIDR blocks, e.g. [\"203.0.113.7/32\", \"198.51.100.0/24\"]. The demo accepts ANY login code, so do not leave this open to the world unless the data is disposable."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "ssh_key_name" {
  description = "Name of an existing EC2 key pair. Leave empty to create the server with no SSH access at all (the app does not need it)."
  type        = string
  default     = ""
}

variable "domain" {
  description = "Optional domain with its DNS A record already pointing at the server's IP, for automatic HTTPS. Leave empty to use the IP over http."
  type        = string
  default     = ""
}

variable "branch" {
  description = "Branch of the project repository to deploy."
  type        = string
  default     = "claude/festive-allen-4dinya"
}
