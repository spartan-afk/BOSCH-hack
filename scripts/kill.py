#!/usr/bin/env python3
"""Emergency kill switch script.

Can be run via `python -m platform.kill` or directly.
Bypasses the API and directly cleans up the cluster using the K8s client.
"""
from __future__ import annotations

import logging
import sys

from app.monitor.k8s_client import K8sClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    logger.warning("🚨 EMERGENCY KILL SWITCH INITIATED 🚨")
    
    try:
        k8s = K8sClient()
    except Exception as e:
        logger.error(f"Failed to initialize K8s client: {e}")
        sys.exit(1)
        
    namespace = "boutique"
        
    # 1. Delete all chaos CRDs
    logger.info(f"Scanning for managed Chaos Mesh CRDs in namespace '{namespace}'...")
    try:
        deleted_count = k8s.delete_all_chaos_crds(namespace)
        logger.info(f"✅ Deleted {deleted_count} Chaos Mesh CRDs")
    except Exception as e:
        logger.error(f"❌ Failed to delete CRDs: {e}")
        
    # 2. To completely restore replicas, we'd need the DB state. 
    # For this CLI fallback, we'll try to use the DB to find the last known state if possible,
    # or just log a warning.
    logger.info("Note: To restore exact replica counts, check the dashboard or app logs.")
    logger.info("Kill switch execution complete.")
    
    
if __name__ == "__main__":
    main()
