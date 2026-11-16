"""Fail-closed offline checks for the production Compose topology.

The checker accepts the checked-in production YAML or a Docker Compose JSON
render saved by an operator. It never invokes Docker, reads dotenv files, or
prints rendered environment values.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INTERNAL_SERVICES = {"api", "worker", "portal", "redis", "linkedin-scraper"}
REQUIRED_SETTINGS = {
    "ENVIRONMENT: production",
    'LEAD_ENGINE_DEV_AUTH_BYPASS: "0"',
    'LOCAL_AUTHENTICATION_FLAG: "0"',
    'LOCAL_VALIDATION_MODE: "0"',
    'REQUIRE_DISTRIBUTED_RATE_LIMIT: "1"',
    'REQUIRE_REDIS_FOR_API_READY: "1"',
    'JOB_QUEUE_FAIL_CLOSED: "1"',
}
REQUIRED_IMAGE_INPUTS = (
    "LEADX_PYTHON_BASE_IMAGE",
    "LEADX_NODE_BASE_IMAGE",
    "LEADX_PROXY_IMAGE",
    "LEADX_REDIS_IMAGE",
)


def _service_blocks(text: str) -> dict[str, str]:
    services_start = re.search(r"(?m)^services:\s*$", text)
    if not services_start:
        return {}
    body = text[services_start.end():]
    end = re.search(r"(?m)^\S", body)
    if end:
        body = body[:end.start()]
    matches = list(re.finditer(r"(?m)^  ([A-Za-z0-9][A-Za-z0-9_-]*):\s*$", body))
    return {
        match.group(1): body[match.end(): matches[index + 1].start() if index + 1 < len(matches) else len(body)]
        for index, match in enumerate(matches)
    }


def _check_text(text: str, *, caddyfile_exists: bool = True) -> list[str]:
    blockers: list[str] = []
    blocks = _service_blocks(text)
    if not blocks:
        blockers.append("services_missing")
    if "reverse-proxy" not in blocks:
        blockers.append("reverse_proxy_missing")
    for service, block in blocks.items():
        if re.search(r"(?m)^    ports:\s*$", block) and service != "reverse-proxy":
            blockers.append(f"public_port_binding:{service}")
        if service in INTERNAL_SERVICES and not re.search(r"networks:\s*\[.*internal", block):
            blockers.append(f"internal_network_missing:{service}")
    for setting in REQUIRED_SETTINGS:
        if setting not in text:
            blockers.append(f"production_setting_missing:{setting.split(':', 1)[0]}")
    if "internal:\n    internal: true" not in text:
        blockers.append("internal_network_not_isolated")
    if "env_file:" in text or ".env" in text:
        blockers.append("production_compose_reads_dotenv")
    if "latest" in text.lower():
        blockers.append("floating_latest")
    if not caddyfile_exists:
        blockers.append("reverse_proxy_config_missing:deploy/Caddyfile")
    for variable in REQUIRED_IMAGE_INPUTS:
        if f"{variable}:?" not in text:
            blockers.append(f"immutable_image_input_missing:{variable}")
    if 'NEXT_PUBLIC_LEADX_LOCAL_AUTH: "0"' not in text:
        blockers.append("portal_local_auth_not_disabled")
    if "API_UPSTREAM: http://api:8000" not in text:
        blockers.append("portal_internal_api_upstream_missing")
    if "WORKER_ID: ${WORKER_ID:?" not in text:
        blockers.append("unique_worker_identity_required")
    return sorted(set(blockers))


def _json_ports(service: dict[str, Any]) -> bool:
    ports = service.get("ports") or []
    return bool(ports)


def _check_rendered(payload: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    services = payload.get("services")
    if not isinstance(services, dict):
        return ["rendered_services_missing"]
    base_arg_by_service = {
        "api": "PYTHON_BASE_IMAGE",
        "worker": "PYTHON_BASE_IMAGE",
        "portal": "NODE_BASE_IMAGE",
        "linkedin-scraper": "PYTHON_BASE_IMAGE",
    }
    for name, service in services.items():
        if not isinstance(service, dict):
            blockers.append(f"rendered_service_invalid:{name}")
            continue
        if _json_ports(service) and name != "reverse-proxy":
            blockers.append(f"public_port_binding:{name}")
        if name in INTERNAL_SERVICES:
            networks = service.get("networks") or {}
            if "internal" not in (networks if isinstance(networks, (list, dict)) else []):
                blockers.append(f"internal_network_missing:{name}")
        image = service.get("image")
        if image and (":latest" in image or not re.search(r"@sha256:[0-9a-f]{64}$", image, re.IGNORECASE)):
            blockers.append(f"non_immutable_image:{name}")
        build = service.get("build") or {}
        required_arg = base_arg_by_service.get(name)
        if required_arg:
            value = build.get("args", {}).get(required_arg) if isinstance(build, dict) and isinstance(build.get("args"), dict) else None
            if not isinstance(value, str) or not re.search(r"@sha256:[0-9a-f]{64}$", value, re.IGNORECASE):
                blockers.append(f"non_immutable_base_image:{name}")
        if "env_file" in service:
            blockers.append(f"service_env_file_forbidden:{name}")
    return sorted(set(blockers))


def validate_production_topology(path: Path = ROOT / "docker-compose.production.yml") -> list[str]:
    if not path.is_file():
        return [f"missing_compose:{path.name}"]
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return ["rendered_compose_invalid_json"]
        return _check_rendered(payload)
    text = path.read_text(encoding="utf-8", errors="replace")
    return _check_text(text, caddyfile_exists=(path.parent / "deploy" / "Caddyfile").is_file())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check production Compose topology without starting Docker")
    parser.add_argument("--compose", type=Path, default=Path("docker-compose.production.yml"))
    args = parser.parse_args(argv)
    path = args.compose if args.compose.is_absolute() else ROOT / args.compose
    blockers = validate_production_topology(path)
    print(json.dumps({"result": "PASS" if not blockers else "BLOCKED", "blockers": blockers}, sort_keys=True))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
