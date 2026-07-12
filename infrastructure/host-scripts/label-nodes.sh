#!/bin/bash
# label-nodes.sh — Labels de scheduling KubeSecureBox (à lancer une fois)
# Usage : sudo bash label-nodes.sh
set -euo pipefail

KUBECTL="${KUBECTL:-k3s kubectl}"

echo "=== Labels nœuds KubeSecureBox ==="
$KUBECTL label node pi-worker-01 kubesecurebox.io/workload=apps --overwrite
$KUBECTL label node pi-worker-02 kubesecurebox.io/workload=infra --overwrite
echo "OK."
$KUBECTL get nodes -L kubesecurebox.io/workload,node-role.kubernetes.io/control-plane
