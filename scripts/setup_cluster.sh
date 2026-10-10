#!/usr/bin/env bash
set -eo pipefail

echo "Starting Chaos Platform Day 1 Setup..."

# Ensure scripts are executable
chmod +x scripts/*.sh

# 1. Deploy Online Boutique
bash scripts/deploy_boutique.sh

# 2. Install Monitoring
bash scripts/install_monitoring.sh

# 3. Install Chaos Mesh
bash scripts/install_chaos.sh

echo ""
echo "========================================================="
echo "✅ Setup Complete!"
echo "========================================================="
echo "Online Boutique Frontend: http://localhost:30080"
echo "Grafana Dashboard:        http://localhost:30300 (admin / prom-operator)"
echo "Prometheus API:           http://localhost:30090"
echo ""
echo "Next steps:"
echo "1. Activate Python virtualenv: cd backend && source .venv/Scripts/activate"
echo "2. Run backend API: uvicorn app.main:app --reload"
echo "3. Try the API endpoints: http://localhost:8000/docs"
