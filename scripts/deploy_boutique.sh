#!/usr/bin/env bash
set -eo pipefail

echo "Deploying Online Boutique to 'boutique' namespace..."
kubectl create namespace boutique --dry-run=client -o yaml | kubectl apply -f -

# Deploy the standard manifests
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/main/release/kubernetes-manifests.yaml -n boutique

# We need the frontend accessible outside the cluster via the NodePort we configured
# 30080 -> 80
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: frontend-external
  namespace: boutique
spec:
  type: NodePort
  selector:
    app: frontend
  ports:
  - name: http
    port: 80
    targetPort: 8080
    nodePort: 30080
EOF

# Wait for frontend to be ready
echo "Waiting for pods to be ready..."
kubectl wait --for=condition=ready pod -l app=frontend -n boutique --timeout=300s

echo "Applying patches (2 replicas for specific services to allow meaningful pod_kill tests)..."
kubectl scale deployment frontend checkoutservice cartservice paymentservice --replicas=2 -n boutique

echo "Online Boutique deployed successfully!"
