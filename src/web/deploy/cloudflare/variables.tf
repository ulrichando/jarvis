variable "account_id" {
  description = "Cloudflare Account ID (dashboard → your domain → Overview, right sidebar)."
  type        = string
}

variable "zone_id" {
  description = "Cloudflare Zone ID for your domain (same Overview sidebar, below Account ID)."
  type        = string
}

variable "hostname" {
  description = "Public hostname for JARVIS, e.g. jarvis.yourdomain.com."
  type        = string
}

variable "origin_ip" {
  description = "Public IPv4 of the server running the docker compose stack (the VPS)."
  type        = string

  validation {
    condition     = can(regex("^\\d{1,3}(\\.\\d{1,3}){3}$", var.origin_ip))
    error_message = "origin_ip must be a bare IPv4 address (the VPS public IP)."
  }
}

variable "allowed_emails" {
  description = "Emails allowed through Zero Trust Access (your login gate). Each gets a one-time PIN."
  type        = list(string)

  validation {
    condition     = length(var.allowed_emails) > 0
    error_message = "Set at least one allowed email, or the app is locked to nobody."
  }
}
