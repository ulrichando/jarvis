# Cloudflare front door for JARVIS (Terraform)

This provisions the **Cloudflare side** of the deploy: a proxied DNS record
pointing your hostname at the origin server, and a Zero Trust Access application
that authenticates every request **before** it reaches the app's RCE surface.

> Cloudflare is the front door — it does **not** host the app. You still need an
> **origin server** (a VPS running `docker compose up`, see
> `../../../docs/runbook/deploy-online.md`). Its public IP is the `origin_ip`
> below.

## What you provide (5 values → `terraform.tfvars`)

| Value | Where to get it |
|---|---|
| `account_id` | Cloudflare dashboard → your domain → **Overview**, right sidebar |
| `zone_id` | Same Overview sidebar, just below Account ID |
| `hostname` | The subdomain you want, e.g. `jarvis.yourdomain.com` |
| `origin_ip` | The **VPS public IP** (the one piece that needs a server) |
| `allowed_emails` | The email(s) allowed in — each gets a one-time PIN |

Plus an **API token** (not in any file): My Profile → API Tokens → Create →
**Edit zone DNS** template, then add permissions **Account › Access: Apps and
Policies › Edit** and **Zone › Access: Apps and Policies › Edit**, scoped to your
zone.

## Apply

```bash
cp terraform.tfvars.example terraform.tfvars   # fill in your 5 values
export CLOUDFLARE_API_TOKEN=...                 # the token — env only, never a file
terraform init
terraform plan      # review: 1 DNS record, 1 Access policy, 1 Access app
terraform apply
```

## Finish in the dashboard (one click each — kept out of TF on purpose)

These are trivial toggles where a wrong IaC value risks a redirect loop or an
insecure origin leg, so they're manual + visible:

1. **SSL/TLS → Overview → Full (strict).** Requires a real cert on the origin —
   create one next.
2. **SSL/TLS → Origin Server → Create Certificate.** Save the cert + key into
   `src/web/certs/origin.crt` and `src/web/certs/origin.key` on the server (the
   compose `caddy` service mounts that dir). Without this, Full (strict) fails.
3. **Security → WAF.** Leave Cloudflare's managed rules ON. Optionally add a rate
   limit on `/api/*`.
4. **Zero Trust → Settings → Authentication.** "One-time PIN" is on by default
   (emails in `allowed_emails` get a code). Add a Google/GitHub IdP here if you'd
   rather have SSO than PIN.

## Verify

- `https://<hostname>` → Cloudflare Access prompt → your email → app login → JARVIS.
- `curl https://<hostname>/api/health` from outside (no Access cookie) → blocked by Access.

## Destroy

`terraform destroy` removes the DNS record + Access app/policy. The dashboard
toggles above are not managed here — undo them manually if needed.
