#!/usr/bin/env bash
set -euo pipefail

NAMESPACE=${NAMESPACE:-default}
VOTE_LOCAL_PORT=${VOTE_LOCAL_PORT:-8080}
RESULT_LOCAL_PORT=${RESULT_LOCAL_PORT:-8081}

echo ">>> Starting Minikube (if not already running)..."
minikube start --driver=docker

echo ">>> Applying Secrets & Config..."
kubectl apply -f kubernetes/secret-config.yaml

echo ">>> Deploying MySQL (init + StatefulSet)..."
kubectl apply -f kubernetes/mysql-init.yaml
kubectl apply -f kubernetes/mysql.yaml

echo ">>> Waiting for MySQL StatefulSet to be ready..."
kubectl rollout status statefulset/db --timeout=300s
DB_POD="$(kubectl get pods -l app=db -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [[ -n "${DB_POD}" ]]; then
  kubectl wait --for=condition=Ready "pod/${DB_POD}" --timeout=300s || true
fi

echo ">>> Deploying application services (NodePort + Deployments)..."
kubectl apply -f kubernetes/app.yaml

echo ">>> Waiting for app rollouts..."
kubectl rollout status deploy/vote --timeout=300s || {
  echo "vote rollout failed"; kubectl describe deploy/vote || true
  kubectl logs -l app=vote --tail=200 --all-containers || true; exit 1; }
kubectl rollout status deploy/result --timeout=300s || {
  echo "result rollout failed"; kubectl describe deploy/result || true
  kubectl logs -l app=result --tail=200 --all-containers || true; exit 1; }

# -------- Localhost port-forward (integrated) --------
kill_if_running () {
  local pidfile="$1"
  if [[ -f "$pidfile" ]]; then
    local pid; pid="$(cat "$pidfile" || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" || true; sleep 1; kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
  fi
}

echo ">>> Starting localhost port-forwards (127.0.0.1:${VOTE_LOCAL_PORT}/${RESULT_LOCAL_PORT})..."
kill_if_running ".pf-vote.pid"
kill_if_running ".pf-result.pid"

# bind to localhost (default); use --address=0.0.0.0 if you want LAN access
nohup kubectl -n "$NAMESPACE" port-forward svc/vote   "${VOTE_LOCAL_PORT}:80"   >/tmp/pf-vote.log   2>&1 &
echo $! > .pf-vote.pid
nohup kubectl -n "$NAMESPACE" port-forward svc/result "${RESULT_LOCAL_PORT}:80" >/tmp/pf-result.log 2>&1 &
echo $! > .pf-result.pid

# -------- Helpful URLs + health checks --------
MINIKUBE_IP="$(minikube ip)"
VOTE_NODEPORT="http://${MINIKUBE_IP}:30080"
RESULT_NODEPORT="http://${MINIKUBE_IP}:30081"
VOTE_LOCAL="http://127.0.0.1:${VOTE_LOCAL_PORT}"
RESULT_LOCAL="http://127.0.0.1:${RESULT_LOCAL_PORT}"

wait_url () {
  local url="$1" name="$2"
  for i in $(seq 1 60); do
    if curl -sf "${url}/api/health" >/dev/null; then
      echo " ${name}: OK -> ${url}"
      return 0
    fi
    sleep 2
  done
  echo " ${name}: not reachable at ${url} (check /tmp/pf-*.log)"
  return 1
}

echo ">>> Checking localhost endpoints..."
wait_url "$VOTE_LOCAL"   "vote"
wait_url "$RESULT_LOCAL" "result"

echo ">>> Open in your browser:"
echo "  $VOTE_LOCAL   (vote – Belgian Malinois vs German Shepherd)"
echo "  $RESULT_LOCAL (result)"

echo ">>> Also available via NodePort (if you prefer):"
echo "  $VOTE_NODEPORT"
echo "  $RESULT_NODEPORT"

echo ">>> Done."
