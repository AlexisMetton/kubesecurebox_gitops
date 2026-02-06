# KubeSecureBox GitOps

Repository GitOps pour le cluster Kubernetes KubeSecureBox sur Raspberry Pi.

## Architecture

```
Votre PC (Cursor) --> GitHub --> ArgoCD --> Cluster K3s
                                              |
                                              ├── NFS Provisioner (StorageClass)
                                              ├── Sealed Secrets (gestion secrets)
                                              └── Vos Applications
```

## Configuration du cluster

| Element | Valeur |
|---------|--------|
| IP Tailscale Master | `100.x.x.x` |
| Chemin NFS | `/{chemin-volume-persistant-pour-kubernetes}` |
| K3s Version | `{versionk3s}` |

## Structure du repository

```
kubesecurebox_gitops/
├── README.md
├── argocd/
│   ├── install/           # Installation ArgoCD
│   └── apps/              # Applications ArgoCD (App of Apps)
├── infrastructure/
│   ├── nfs-provisioner/   # StorageClass NFS (HDD 4To)
│   ├── sealed-secrets/    # Gestion des secrets chiffres
│   └── ingress-nginx/     # Ingress Controller (a venir)
└── apps/                  # Vos applications Kubernetes
```

## Installation initiale

### 1. Installer ArgoCD

```bash
# Appliquer les manifests ArgoCD
kubectl apply -k argocd/install/

# Attendre qu'ArgoCD soit pret
kubectl wait --for=condition=available deployment/argocd-server -n argocd --timeout=300s

# Recuperer le mot de passe admin initial
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
echo  # Nouvelle ligne

# Acceder a l'interface ArgoCD
kubectl port-forward svc/argocd-server -n argocd 8080:443
# Ouvrir https://localhost:8080 (user: admin)
```

### 2. Pousser le repo sur GitHub

```bash
# Initialiser Git (si pas deja fait)
git init
git remote add origin {url_repo}

# Premier commit
git add .
git commit -m "Initial GitOps structure"
git branch -M main
git push -u origin main
```

### 3. Connecter ArgoCD au repo

```bash
# Appliquer l'application racine
kubectl apply -f argocd/apps/root-app.yaml

# ArgoCD va automatiquement deployer :
# - NFS Provisioner
# - Sealed Secrets
```

### 4. Verifier l'installation

```bash
# Verifier les applications ArgoCD
kubectl get applications -n argocd

# Verifier le NFS Provisioner
kubectl get pods -n nfs-provisioner
kubectl get storageclass

# Verifier Sealed Secrets
kubectl get pods -n kube-system -l app.kubernetes.io/name=sealed-secrets
```

## Utilisation des Sealed Secrets

Les Sealed Secrets permettent de stocker des secrets chiffres dans ce repo public.

### Installer kubeseal sur votre PC

```bash
# Windows (Scoop)
scoop install kubeseal

# Windows (Chocolatey)
choco install kubeseal

# Linux
wget https://github.com/bitnami-labs/sealed-secrets/releases/download/v0.24.5/kubeseal-0.24.5-linux-amd64.tar.gz
tar -xzf kubeseal-0.24.5-linux-amd64.tar.gz
sudo mv kubeseal /usr/local/bin/
```

### Creer un secret chiffre

```bash
# 1. Creer un secret Kubernetes (dry-run, ne pas appliquer)
kubectl create secret generic mon-secret \
  --from-literal=username=admin \
  --from-literal=password=SuperSecret123 \
  --dry-run=client -o yaml > secret.yaml

# 2. Chiffrer avec kubeseal
kubeseal --format yaml < secret.yaml > sealed-secret.yaml

# 3. Supprimer le secret en clair !
rm secret.yaml

# 4. Commiter le sealed secret (peut etre dans le repo public)
git add sealed-secret.yaml
git commit -m "Add sealed secret"
git push
```

### Backup de la cle privee (important !)

Si vous perdez la cle privee, vous ne pourrez plus dechiffrer vos secrets.

```bash
# Sauvegarder la cle privee (a garder en lieu sur, PAS dans Git !)
kubectl get secret -n kube-system -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml > sealed-secrets-backup.yaml
```

## Workflow quotidien

1. Modifier les manifests dans Cursor
2. `git add . && git commit -m "Description" && git push`
3. ArgoCD detecte et applique automatiquement (~3 min)
4. Verifier dans l'UI ArgoCD ou avec `kubectl`

## Ajouter une nouvelle application

1. Creer un dossier dans `apps/mon-app/`
2. Ajouter vos manifests Kubernetes (Deployment, Service, etc.)
3. Commit et push
4. ArgoCD deploie automatiquement

## Troubleshooting

### ArgoCD ne synchronise pas

```bash
# Forcer la synchronisation
kubectl -n argocd patch application root --type merge -p '{"metadata": {"annotations": {"argocd.argoproj.io/refresh": "hard"}}}'
```

### NFS ne fonctionne pas

```bash
# Verifier que le serveur NFS est accessible
kubectl run test-nfs --image=busybox --rm -it --restart=Never -- \
  sh -c "mount -t nfs 100.66.46.6:/mnt/storage/nfs-shares/k8s /mnt && ls /mnt"
```

### Voir les logs ArgoCD

```bash
kubectl logs -n argocd deployment/argocd-server
kubectl logs -n argocd deployment/argocd-application-controller
```

## Liens utiles

- [ArgoCD Documentation](https://argo-cd.readthedocs.io/)
- [Sealed Secrets](https://github.com/bitnami-labs/sealed-secrets)
- [NFS Subdir External Provisioner](https://github.com/kubernetes-sigs/nfs-subdir-external-provisioner)
