#!/usr/bin/env bash
set -euo pipefail

# -------- stop localhost port-forwards first --------
stop_pf () {
  local f="$1"
  if [[ -f "$f" ]]; then
    local pid; pid="$(cat "$f" || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" || true; sleep 1; kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$f"
  fi
}
stop_pf .pf-vote.pid
stop_pf .pf-result.pid

echo ">>> Deleting Kubernetes resources..."
kubectl delete -f kubernetes/app.yaml --ignore-not-found
kubectl delete -f kubernetes/mysql.yaml --ignore-not-found
kubectl delete -f kubernetes/mysql-init.yaml --ignore-not-found
kubectl delete -f kubernetes/secret-config.yaml --ignore-not-found

echo ">>> Deleting PersistentVolumeClaims (DB data will be erased)..."
# Try label (if you use one); otherwise delete by name prefix
kubectl delete pvc -l app=db --ignore-not-found || true
# Fallback: delete PVCs created by the StatefulSet volumeClaimTemplates
kubectl get pvc -o name | grep -E '^persistentvolumeclaim/mysql-data' | xargs -r kubectl delete

echo ">>> Stopping and deleting Minikube cluster..."
minikube stop || true
minikube delete --all --purge || true

echo ">>> Removing local Docker images related to project (ignore errors if not present)..."
docker rmi -f docker.io/msaeedghaderi/votes-api:v1 2>/dev/null || true
docker rmi -f docker.io/msaeedghaderi/votes-api:v2 2>/dev/null || true
docker rmi -f docker.io/msaeedghaderi/results-api:v1 2>/dev/null || true
docker rmi -f docker.io/msaeedghaderi/results-api:v2 2>/dev/null || true
docker rmi -f mysql:8.0 node:20-alpine python:3.12-slim 2>/dev/null || true

echo ">>> Full cleanup complete."
