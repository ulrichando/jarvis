# JARVIS — Cloudflare front door (DNS + Zero Trust Access) as code.
# Cloudflare proxies a hostname to your origin server (the docker compose stack)
# and authenticates every request BEFORE it reaches the app's RCE surface.
#
# Apply:  export CLOUDFLARE_API_TOKEN=...   (never put the token in a file)
#         terraform init && terraform apply
# Token scope (My Profile → API Tokens → Create): for your zone —
#   Zone:DNS:Edit, Account:Access: Apps and Policies:Edit, Zone:Zone Settings:Read.

terraform {
  required_version = ">= 1.6"
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }
  }
}

# Reads CLOUDFLARE_API_TOKEN from the environment.
provider "cloudflare" {}

# 1) DNS — point the hostname at the origin, PROXIED (orange cloud) so Cloudflare's
#    edge (TLS termination, WAF, Access) sits in front of the origin server.
resource "cloudflare_dns_record" "jarvis" {
  zone_id = var.zone_id
  name    = var.hostname
  type    = "A"
  content = var.origin_ip
  ttl     = 1 # 1 = automatic (required; ignored while proxied)
  proxied = true
  comment = "JARVIS web origin (managed by Terraform)"
}

# 2) Access policy — allow ONLY these emails. Account-scoped. Cloudflare emails a
#    one-time PIN to verify (no IdP setup needed; add Google/GitHub IdP later if
#    you want SSO instead of OTP).
resource "cloudflare_zero_trust_access_policy" "allow_emails" {
  account_id = var.account_id
  name       = "JARVIS — allowed users"
  decision   = "allow"

  include = [for addr in var.allowed_emails : {
    email = {
      email = addr
    }
  }]
}

# 3) Access application — the gate. Authenticates before a request ever reaches
#    the app (and its /api/workspace/[id]/exec arbitrary-shell surface). This is
#    the non-negotiable layer; the app's own login is the second.
resource "cloudflare_zero_trust_access_application" "jarvis" {
  zone_id          = var.zone_id
  name             = "JARVIS"
  domain           = var.hostname
  type             = "self_hosted"
  session_duration = "24h"

  policies = [{
    id         = cloudflare_zero_trust_access_policy.allow_emails.id
    precedence = 1
  }]
}

output "next_steps" {
  value = <<-EOT
    DNS + Access applied for ${var.hostname} → ${var.origin_ip}.
    Finish in the dashboard (one-click each, see README):
      • SSL/TLS → Overview → Full (strict)
      • SSL/TLS → Origin Server → create an Origin Certificate → put it in src/web/certs/
      • Security → WAF → leave managed rules ON
  EOT
}
