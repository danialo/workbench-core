"""Demo backend for testing without real infrastructure."""

from __future__ import annotations

import random
import time

from workbench.backends.base import BackendError, DiagnosticInfo, ExecutionBackend

_DEMO_TARGETS = {
    "demo-host-1": {
        "type": "host",
        "hostname": "demo-host-1.example.com",
        "ip": "10.0.1.10",
        "os": "Ubuntu 22.04",
        "status": "online",
    },
    "demo-host-2": {
        "type": "host",
        "hostname": "demo-host-2.example.com",
        "ip": "10.0.1.11",
        "os": "CentOS 9",
        "status": "online",
    },
    "demo-service-1": {
        "type": "service",
        "name": "api-gateway",
        "endpoint": "https://api.example.com",
        "port": 443,
        "status": "healthy",
    },
    "demo-service-2": {
        "type": "service",
        "name": "auth-service",
        "endpoint": "https://auth.example.com",
        "port": 8443,
        "status": "degraded",
    },
}

_DEMO_DIAGNOSTICS = {
    "host": [
        DiagnosticInfo("ping", "Send ICMP ping to host", "host", {"type": "object", "properties": {"count": {"type": "integer", "default": 4}}}),
        DiagnosticInfo("traceroute", "Trace network route to host", "host", {}),
        DiagnosticInfo("dns_lookup", "Resolve DNS records for host", "host", {"type": "object", "properties": {"record_type": {"type": "string", "default": "A"}}}),
        DiagnosticInfo("port_check", "Check if ports are open on host", "host", {"type": "object", "properties": {"ports": {"type": "array", "items": {"type": "integer"}}}}),
        DiagnosticInfo("log_tail", "Tail recent log lines from host", "host", {"type": "object", "properties": {"lines": {"type": "integer", "default": 50}, "service": {"type": "string"}}}),
    ],
    "service": [
        DiagnosticInfo("service_status", "Check service health and uptime", "service", {}),
        DiagnosticInfo("ping", "Send HTTP health check to service", "service", {}),
        DiagnosticInfo("dns_lookup", "Resolve DNS for service endpoint", "service", {}),
        DiagnosticInfo("log_tail", "Tail recent service logs", "service", {"type": "object", "properties": {"lines": {"type": "integer", "default": 50}}}),
    ],
}


class DemoBackend(ExecutionBackend):
    """
    A demo backend that returns simulated diagnostic results.

    Useful for testing the full orchestrator flow without real infrastructure.
    """

    async def resolve_target(self, target: str, **kwargs) -> dict:
        info = _DEMO_TARGETS.get(target)
        if not info:
            raise BackendError(f"Unknown target: {target}", code="target_not_found")
        return dict(info)

    async def list_diagnostics(self, target: str, **kwargs) -> list[DiagnosticInfo]:
        info = _DEMO_TARGETS.get(target)
        if not info:
            raise BackendError(f"Unknown target: {target}", code="target_not_found")
        target_type = info.get("type", "host")
        return list(_DEMO_DIAGNOSTICS.get(target_type, []))

    async def run_diagnostic(self, action: str, target: str, **kwargs) -> dict:
        info = _DEMO_TARGETS.get(target)
        if not info:
            raise BackendError(f"Unknown target: {target}", code="target_not_found")

        generators = {
            "ping": self._gen_ping,
            "traceroute": self._gen_traceroute,
            "dns_lookup": self._gen_dns_lookup,
            "port_check": self._gen_port_check,
            "service_status": self._gen_service_status,
            "log_tail": self._gen_log_tail,
        }

        gen = generators.get(action)
        if not gen:
            raise BackendError(f"Unknown diagnostic: {action}", code="unknown_diagnostic")

        return gen(target, info, **kwargs)

    async def run_shell(self, command: str, target: str, **kwargs) -> dict:
        info = _DEMO_TARGETS.get(target)
        if not info:
            raise BackendError(f"Unknown target: {target}", code="target_not_found")
        return {
            "exit_code": 0,
            "stdout": f"[demo] $ {command}\n(simulated output for {target})\n",
            "stderr": "",
            "duration_ms": random.randint(50, 500),
        }

    # --- Result generators ---

    def _gen_ping(self, target: str, info: dict, **kwargs) -> dict:
        count = kwargs.get("count", 4)
        ip = info.get("ip", info.get("endpoint", "unknown"))
        times = [round(random.uniform(0.5, 25.0), 2) for _ in range(count)]
        return {
            "target": target,
            "ip": ip,
            "packets_sent": count,
            "packets_received": count,
            "packet_loss_pct": 0.0,
            "rtt_min_ms": min(times),
            "rtt_avg_ms": round(sum(times) / len(times), 2),
            "rtt_max_ms": max(times),
            "times_ms": times,
        }

    def _gen_traceroute(self, target: str, info: dict, **kwargs) -> dict:
        hops = []
        for i in range(1, random.randint(5, 12)):
            hops.append({
                "hop": i,
                "ip": f"10.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}",
                "rtt_ms": round(random.uniform(0.5, 50.0) * i, 2),
                "hostname": f"hop-{i}.network.example.com",
            })
        return {"target": target, "hops": hops}

    def _gen_dns_lookup(self, target: str, info: dict, **kwargs) -> dict:
        hostname = info.get("hostname", info.get("endpoint", target))
        return {
            "query": hostname,
            "record_type": kwargs.get("record_type", "A"),
            "answers": [
                {"type": "A", "value": info.get("ip", "10.0.1.1"), "ttl": 300},
            ],
            "nameserver": "10.0.0.2",
            "response_time_ms": round(random.uniform(1, 20), 2),
        }

    def _gen_port_check(self, target: str, info: dict, **kwargs) -> dict:
        ports = kwargs.get("ports", [22, 80, 443])
        results = []
        for port in ports:
            results.append({
                "port": port,
                "state": "open" if random.random() > 0.1 else "filtered",
                "service": {22: "ssh", 80: "http", 443: "https"}.get(port, "unknown"),
            })
        return {"target": target, "port_results": results}

    def _gen_service_status(self, target: str, info: dict, **kwargs) -> dict:
        return {
            "target": target,
            "service": info.get("name", target),
            "status": info.get("status", "unknown"),
            "uptime_seconds": random.randint(3600, 864000),
            "last_restart": "2026-02-08T10:30:00Z",
            "version": "2.4.1",
            "connections_active": random.randint(10, 500),
            "cpu_pct": round(random.uniform(1, 45), 1),
            "memory_mb": random.randint(128, 2048),
        }

    def _gen_log_tail(self, target: str, info: dict, **kwargs) -> dict:
        lines = kwargs.get("lines", 10)
        levels = ["INFO", "INFO", "INFO", "WARN", "DEBUG", "ERROR"]
        log_lines = []
        ts = int(time.time())
        for i in range(min(lines, 20)):
            level = random.choice(levels)
            log_lines.append(
                f"2026-02-09T{10+i//60:02d}:{i%60:02d}:00Z [{level}] "
                f"Sample log message {i+1} from {target}"
            )
        return {"target": target, "lines": log_lines, "total_available": lines}
