# LeadX login + brand hardening

- Removed serial `/health` + `/crm/me` bootstrap from client login.
- Workspace bootstrap runs once per authenticated browser session instead of on every portal navigation.
- `/backend` remains the primary same-origin API path; GitHub Codespaces now gets a forwarded `-8000` browser fallback when the proxy returns 404/502/503.
- Workspace bootstrap timeout is bounded at 6.5s; password login is bounded at 12s.
- Tenant ID/name remains cached locally after successful bootstrap.
- New visual identity: LeadX Deep Teal / Tangerine — deep teal for product identity, tangerine for opportunity/live signals, ink-first surfaces. Aurora/indigo/cyan branding is removed.
