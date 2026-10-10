#!/usr/bin/env bash
set -eo pipefail

echo "Installing kube-prometheus-stack..."

# Add prometheus-community helm repo
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

# Create namespace
kubectl create ns monitoring --dry-run=client -o yaml | kubectl apply -f -

# Install Prometheus/Grafana
# We disable Alertmanager and other heavy components to save resources on Kind
helm install prom prometheus-community/kube-prometheus-stack -n monitoring \
  --set alertmanager.enabled=false \
  --set grafana.enabled=true \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false

# Expose Grafana on NodePort 30300
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: grafana-external
  namespace: monitoring
spec:
  type: NodePort
  selector:
    app.kubernetes.io/name: grafana
  ports:
  - name: http
    port: 80
    targetPort: 3000
    nodePort: 30300
EOF

# Expose Prometheus on NodePort 30090 (optional, useful for debugging)
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: prometheus-external
  namespace: monitoring
spec:
  type: NodePort
  selector:
    app.kubernetes.io/name: prometheus
  ports:
  - name: web
    port: 9090
    targetPort: 9090
    nodePort: 30090
EOF

echo "Waiting for Prometheus stack to be ready..."
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=prometheus -n monitoring --timeout=300s

echo "Monitoring stack installed successfully!"
echo "Grafana accessible at http://localhost:30300 (admin / prom-operator)"
