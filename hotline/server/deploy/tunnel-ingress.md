# Cloudflare tunnel ingress for the hotline

Add to the existing tunnel config on the Pi (BEFORE the catch-all rule),
then restart cloudflared. Subdomain decision per spec §10.

    - hostname: phone.thekartoff.com
      service: http://127.0.0.1:9100

Plus a DNS route: `cloudflared tunnel route dns <tunnel> phone.thekartoff.com`
(or the dashboard equivalent). WebSockets pass through by default.
Verify after: `curl https://phone.thekartoff.com/healthz` → {"ok": true}
from OUTSIDE the LAN (phone hotspot).
