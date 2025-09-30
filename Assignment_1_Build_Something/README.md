# Voting App – Kubernetes Deployment

This project is a simple voting application deployed on Kubernetes.
It includes:
- A **voting frontend** (`frontend_vote`) where users can vote (Python/Flask).
- A **results frontend** (`frontend_votes_result`) to display results (Node.js/Express + Angular).
- A **MySQL database** to store votes.
- Kubernetes manifests and helper scripts to automate deployment.

---

## Project Structure
```
Assignment_1_Build_Something/
│── frontend_vote/             # Python Flask voting app
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
│
│── frontend_votes_result/     # Node.js results app with Angular frontend
│   ├── server.js
│   ├── package.json
│   ├── Dockerfile
│   └── views/
│
│── kubernetes/                # Kubernetes manifests
│   ├── secret-config.yaml
│   ├── mysql-init.yaml
│   ├── mysql.yaml
│   └── app.yaml
│
│── startup.sh                 # Script to start cluster and deploy apps
│── cleanup.sh                 # Script to tear down everything
```

---

## Prerequisites
- [Docker](https://www.docker.com/)  
- [Minikube](https://minikube.sigs.k8s.io/docs/)  
- [kubectl](https://kubernetes.io/docs/tasks/tools/)  

---

## Running the Application
Make sure that startup.sh is executable.

### 1. Start the cluster and deploy
./startup.sh

### 2. Access the apps
- **Vote App** → [http://127.0.0.1:8080](http://127.0.0.1:8080)  
- **Result App** → [http://127.0.0.1:8081](http://127.0.0.1:8081)  

Alternatively, via NodePort (on Minikube IP):
- Vote: `http://<minikube-ip>:30080`  
- Results: `http://<minikube-ip>:30081`  

---

## 🧹 Cleaning Up
Make sure that startup.sh is executable.

### 1. Stop and remove everything
./cleanup.sh

