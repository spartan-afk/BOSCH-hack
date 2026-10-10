"""Read-only Docker helpers, always scoped to this Compose project's label."""

import json
import subprocess


def list_project_containers(project: str, label_key: str = "com.docker.compose.project") -> list[dict]:
    """Containers (running or stopped) carrying this Compose project's label."""
    out = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"label={label_key}={project}", "--format", "{{json .}}"],
        capture_output=True, text=True, timeout=20, check=True,
    ).stdout
    containers = []
    for line in out.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        labels = dict(kv.split("=", 1) for kv in row.get("Labels", "").split(",") if "=" in kv)
        containers.append({
            "name": row.get("Names"),
            "service": labels.get("com.docker.compose.service"),
            "image": row.get("Image"),
            "state": row.get("State"),
            "status": row.get("Status"),
            "ports": row.get("Ports"),
        })
    return containers
