---
name: port-recon
description: Recon ports lab via nmap (profil prudent, top ports).
---

1. Vérifie l'allowlist.
2. `scan_start("nmap", host_or_ip)` — le runner utilise un profil limité (-sV -T4 --top-ports 100).
3. Présente ports/services depuis `scan_report` uniquement.
4. Ne propose pas de scans agressifs hors profil tool.
