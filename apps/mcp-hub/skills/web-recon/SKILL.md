---
name: web-recon
description: Enchaînement de recon web lab (whatweb → httpx → nikto) sur une cible allowlistée.
---

Pour une demande de recon web sur une cible autorisée :

1. `pentest_allowlist_list` — confirmer la cible.
2. `scan_start("whatweb", target)` → attendre le rapport.
3. `scan_start("httpx", target)` → statut HTTP / titre.
4. Si pertinent : `scan_start("nikto", url)` sur l'URL https.
5. Synthèse structurée à partir des `scan_report` uniquement.
