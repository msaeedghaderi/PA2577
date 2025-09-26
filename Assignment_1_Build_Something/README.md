## Kubernetes: 

### Run:
kubectl create -f kubernetes/

The vote web app is then available on http://localhost:5678 on each host of the cluster, the result web app is available on http://localhost:5001.

### Clean up:
kubectl delete -f kubernetes/


## Docker local host machine:

### Run:
docker compose up

The vote app will be running at http://localhost:5678, and the results will be at http://localhost:5001.
