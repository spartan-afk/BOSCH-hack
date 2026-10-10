"""Adds and removes Toxiproxy latency via its HTTP API.

Only the orchestrator calls this, and only after safety.validate_experiment()
has accepted the experiment. Agents have no path to it.
"""

import requests

from chaos.config import allowed_links

TOXIC_NAME = "chaos_latency"


class ExecutorError(Exception):
    pass


class Executor:
    def __init__(self, cfg: dict):
        self.api = cfg["toxiproxy"]["api_url"]
        self.links = {link["name"]: link for link in allowed_links(cfg)}

    def _req(self, method: str, path: str, **kw) -> requests.Response:
        try:
            return requests.request(method, f"{self.api}{path}", timeout=5, **kw)
        except requests.RequestException as e:
            raise ExecutorError(f"Toxiproxy API unreachable at {self.api}: {e}")

    def reachable(self) -> bool:
        try:
            return self._req("GET", "/version").status_code == 200
        except ExecutorError:
            return False

    def ensure_proxies(self) -> None:
        """Create (or re-enable) one proxy per allowed link. Idempotent."""
        for link in self.links.values():
            resp = self._req("GET", f"/proxies/{link['proxy']}")
            if resp.status_code == 404:
                resp = self._req("POST", "/proxies", json={
                    "name": link["proxy"], "listen": link["listen"], "upstream": link["upstream"], "enabled": True})
                if resp.status_code not in (200, 201, 409):
                    raise ExecutorError(f"could not create proxy {link['proxy']}: {resp.text}")
            elif not resp.json().get("enabled", True):
                self._req("POST", f"/proxies/{link['proxy']}", json={"enabled": True})

    def active_toxics(self) -> list[dict]:
        resp = self._req("GET", "/proxies")
        return [{"proxy": name, **t} for name, p in resp.json().items() for t in p.get("toxics", [])]

    def add_latency(self, link_name: str, latency_ms: int) -> dict:
        if link_name not in self.links:
            raise ExecutorError(f"link {link_name!r} is not in config allowed_links")
        if self.active_toxics():
            raise ExecutorError("a toxic is already active; only one target at a time")
        proxy = self.links[link_name]["proxy"]
        resp = self._req("POST", f"/proxies/{proxy}/toxics", json={
            "name": TOXIC_NAME, "type": "latency", "stream": "upstream", "toxicity": 1.0,
            "attributes": {"latency": int(latency_ms), "jitter": 0}})
        if resp.status_code not in (200, 201):
            raise ExecutorError(f"could not add latency toxic: {resp.text}")
        return resp.json()

    def remove_all(self) -> list[str]:
        """Remove every toxic and re-enable every proxy. Safe to run any number of times."""
        removed = []
        for name, proxy in self._req("GET", "/proxies").json().items():
            for toxic in proxy.get("toxics", []):
                resp = self._req("DELETE", f"/proxies/{name}/toxics/{toxic['name']}")
                if resp.status_code in (200, 204, 404):
                    removed.append(f"{name}/{toxic['name']}")
            if not proxy.get("enabled", True):
                self._req("POST", f"/proxies/{name}", json={"enabled": True})
        if self.active_toxics():
            raise ExecutorError("toxics still present after rollback")
        return removed
