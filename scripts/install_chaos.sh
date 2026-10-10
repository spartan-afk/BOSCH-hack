#!/usr/bin/env bash
set -eo pipefail

echo "Installing Chaos Mesh..."

# Add helm repo
helm repo add chaos-mesh https://charts.chaos-mesh.org
helm repo update

# Create namespace
kubectl create ns chaos-mesh --dry-run=client -o yaml | kubectl apply -f -

# Install Chaos Mesh
helm install chaos-mesh chaos-mesh/chaos-mesh -n chaos-mesh \
  --set chaosDaemon.runtime=containerd \
  --set chaosDaemon.socketPath=/run/containerd/containerd.sock

echo "Waiting for Chaos Mesh to be ready..."
kubectl wait --for=condition=ready pod -l app.kubernetes.io/instance=chaos-mesh -n chaos-mesh --timeout=300s

echo "Chaos Mesh installed successfully!"
