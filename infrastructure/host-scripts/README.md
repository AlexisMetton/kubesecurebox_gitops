# Scripts hôte KubeSecureBox
# ==========================
# Fichiers à copier sur les Pi (Ansible ou manuel). Non déployés par ArgoCD.
#
## Installation (pi-master, une fois)
#
```bash
sudo mkdir -p /etc/kubesecurebox /usr/local/bin
# Webhook Discord #général (Lynis, SSH) — même URL que Grafana ou webhook dédié
sudo nano /etc/kubesecurebox/discord-webhook.url

sudo cp discord-notify.sh lynis-scan.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/discord-notify.sh /usr/local/bin/lynis-scan.sh

# Cron Lynis (root) — déjà présent si Ansible :
# 0 3 * * 0 /usr/local/bin/lynis-scan.sh
```

## label-nodes.sh

À lancer **une fois** sur pi-master après sync GitOps (scheduling Loki/Prom/Grafana) :

```bash
sudo bash label-nodes.sh
```

## cleanup-pi-master.sh

Retire OpenCode, Telegram et copies locales stock-analyzer. **À lancer sur pi-master** :

```bash
bash cleanup-pi-master.sh
```

## Lynis sans score Discord

Le score est lu depuis le dernier `lynis_report_*.txt` (pas `report.dat`).
Réinstaller `lynis-scan.sh` si l'ancienne version affichait `/100` vide.
