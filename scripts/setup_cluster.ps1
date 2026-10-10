$ErrorActionPreference = "Stop"

Write-Host "Starting Chaos Platform Day 1 Setup..." -ForegroundColor Green

Write-Host "`n[1/3] Deploying Online Boutique to 'boutique' namespace..." -ForegroundColor Cyan
kubectl create namespace boutique --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/main/release/kubernetes-manifests.yaml -n boutique

$nodePortYaml = @"
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
"@
$nodePortYaml | kubectl apply -f -

Write-Host "Waiting for frontend pods to be ready (this may take a few minutes)..."
kubectl wait --for=condition=ready pod -l app=frontend -n boutique --timeout=300s

Write-Host "Scaling critical services to 2 replicas for meaningful pod_kill tests..."
kubectl scale deployment frontend checkoutservice cartservice paymentservice --replicas=2 -n boutique


Write-Host "`n[2/3] Installing kube-prometheus-stack..." -ForegroundColor Cyan
& "$env:LOCALAPPDATA\Microsoft\WindowsApps\helm.exe" repo add prometheus-community https://prometheus-community.github.io/helm-charts
& "$env:LOCALAPPDATA\Microsoft\WindowsApps\helm.exe" repo update

kubectl create ns monitoring --dry-run=client -o yaml | kubectl apply -f -
& "$env:LOCALAPPDATA\Microsoft\WindowsApps\helm.exe" install prom prometheus-community/kube-prometheus-stack -n monitoring --set alertmanager.enabled=false --set grafana.enabled=true --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false

$grafanaYaml = @"
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
"@
$grafanaYaml | kubectl apply -f -

Write-Host "Waiting for Prometheus stack to be ready..."
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=prometheus -n monitoring --timeout=300s


Write-Host "`n[3/3] Installing Chaos Mesh..." -ForegroundColor Cyan
& "$env:LOCALAPPDATA\Microsoft\WindowsApps\helm.exe" repo add chaos-mesh https://charts.chaos-mesh.org
& "$env:LOCALAPPDATA\Microsoft\WindowsApps\helm.exe" repo update

kubectl create ns chaos-mesh --dry-run=client -o yaml | kubectl apply -f -
& "$env:LOCALAPPDATA\Microsoft\WindowsApps\helm.exe" install chaos-mesh chaos-mesh/chaos-mesh -n chaos-mesh --set chaosDaemon.runtime=containerd --set chaosDaemon.socketPath=/run/containerd/containerd.sock

Write-Host "Waiting for Chaos Mesh to be ready..."
kubectl wait --for=condition=ready pod -l app.kubernetes.io/instance=chaos-mesh -n chaos-mesh --timeout=300s


Write-Host "`n=========================================================" -ForegroundColor Green
Write-Host "✅ Setup Complete!" -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Green
Write-Host "Online Boutique Frontend: http://localhost:30080"
Write-Host "Grafana Dashboard:        http://localhost:30300 (admin / prom-operator)"
Write-Host "`nNext steps:"
Write-Host "1. Activate Python virtualenv: cd backend; .\.venv\Scripts\activate"
Write-Host "2. Run backend API: uvicorn app.main:app --reload"
Write-Host "3. Try the API endpoints: http://localhost:8000/docs"
