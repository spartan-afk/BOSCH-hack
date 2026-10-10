export const API_URL = "http://localhost:8000/api";

export async function fetchGraph() {
  const res = await fetch(`${API_URL}/graph`);
  return res.json();
}

export async function fetchHealth() {
  const res = await fetch(`${API_URL}/health`);
  return res.json();
}

export async function fetchPlan() {
  const res = await fetch(`${API_URL}/plan`);
  return res.json();
}

export async function runExperiment(spec: Record<string, unknown> | any) {
  const validateRes = await fetch(`${API_URL}/experiments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(spec),
  });
  const validateData = await validateRes.json();
  if (validateData.status !== "validated") {
    throw new Error(`Validation failed: ${validateData.reasons?.join(", ")}`);
  }

  const runRes = await fetch(`${API_URL}/experiments/${validateData.experiment_id}/run`, {
    method: "POST",
  });
  return runRes.json();
}

export async function killSwitch() {
  const res = await fetch(`${API_URL}/kill`, { method: "POST" });
  return res.json();
}
