---
name: wordpress-audit
description: Audit WordPress lab via wpscan quand le site est WP / demandé explicitement.
---

1. Confirme que la cible est dans l'allowlist.
2. Si besoin, `whatweb` / `httpx` pour confirmer WordPress.
3. `scan_start("wpscan", url)` avec l'URL du site.
4. Résume plugins/thèmes/versions signalés dans le rapport — sans inventer d'exploits.
