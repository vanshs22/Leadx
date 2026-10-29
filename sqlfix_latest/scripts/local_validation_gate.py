"""Repeatable, local-only Task 11 release gate.

This command never opts into staging/live benchmark paths.  It runs static,
synthetic, fake-fixture, portal-contract, and Compose checks with bounded
subprocesses, writes structured evidence, and leaves remote evidence
explicitly unverified.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

from release_evidence import EvidenceRecord, EvidenceReport

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / "lead-crm-portal"
DEFAULT_OUTPUT = ROOT / "artifacts" / "local-validation"
CORE_SERVICES = ("redis", "api", "worker", "portal")
REMOTE_CHECKS = (
    "remote Supabase",
    "external providers and Serper",
    "paid enrichment and provider credits",
    "email delivery",
    "Stripe billing",
    "staging/production deployment",
)


def _local_environment() -> dict[str, str]:
    env = os.environ.copy()
    # Explicit values are deliberate: missing credentials must not be treated
    # as permission to contact a remote service.
    explicit_remote_names = {
        "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET", "LEAD_ENGINE_SERVICE_KEY",
        "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "SERPER_API_KEY", "DECODO_API_KEY",
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN",
        "AWS_DEFAULT_REGION", "SES_SMTP_USERNAME", "SES_SMTP_PASSWORD", "WEBHOOK_SECRET",
        "WEBHOOK_SIGNING_SECRET", "UNSUBSCRIBE_SECRET", "INTEGRATION_OAUTH_STATE_SECRET",
        "LEADX_STAGING_URL", "LEADX_STAGING_HEALTH_URL", "LEADX_STAGING_RELEASE_ID",
        "LEADX_TENANT_A_TOKEN", "LEADX_TENANT_B_ID", "LEADX_EMAIL_VALIDATOR_URL",
        "NUMBERS_ONLINE_LOOKUP_URL", "DECODO_API_BASE_URL",
    }
    for name in tuple(env):
        upper_name = name.upper()
        if (
            name in explicit_remote_names
            or upper_name.endswith("_API_KEY")
            or upper_name.endswith("_AUTH_TOKEN")
            or upper_name.startswith("LEADX_PROVIDER_")
        ):
            env[name] = ""
    for name in explicit_remote_names:
        env[name] = ""
    env.update({
        "ENVIRONMENT": "development",
        "LOCAL_VALIDATION_MODE": "true",
        "LOCAL_AUTHENTICATION_FLAG": "true",
        "LOCAL_AUTH_EMAIL": "local@example.test",
        "LOCAL_AUTH_PASSWORD": "synthetic-password",
        "LOCAL_AUTH_SECRET": "local-validation-secret-" + "x" * 32,
        "LOCAL_AUTH_USER_ID": "local-user",
        "LOCAL_AUTH_TENANT_ID": "local-tenant",
        "LOCAL_DURABLE_BACKEND": "sqlite",
        "LOCAL_DURABLE_PATH": "/var/lib/leadx/local-durable.sqlite3",
        "REQUIRE_REDIS_FOR_API_READY": "1",
        "REQUIRE_WORKER_READY": "1",
        "JOB_QUEUE_FAIL_CLOSED": "1",
        "PAID_PROVIDER_ISOLATION": "true",
        "LEADX_PAID_FALLBACK": "off",
        "LEADX_ENABLE_PUBLIC_FREE_VERIFY_APIS": "false",
        "LEADX_PROVIDER_TEST_MODE": "synthetic",
        "LEADX_OFFLINE": "1",
        "NO_NETWORK": "1",
        "LEADX_REQUIRE_EMAIL_VERIFICATION": "false",
        "APP_PUBLIC_URL": "http://localhost:3000",
        "PUBLIC_API_URL": "http://localhost:8000",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    return env


def _tail(text: str, limit: int = 700) -> str:
    return " ".join(text.strip().split())[-limit:] or "completed without output"


def _artifact_name(output_dir: Path, name: str) -> Path:
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in name)
    return output_dir / f"{safe}.log"


def run_command(
    report: EvidenceReport,
    *,
    section: str,
    mode: str,
    name: str,
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    output_dir: Path,
    timeout: int,
) -> bool:
    artifact = _artifact_name(output_dir, name)
    display = " ".join(command)
    try:
        completed = subprocess.run(
            command, cwd=cwd, env=env, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, check=False,
        )
        output = completed.stdout or ""
        artifact.write_text(f"$ {display}\n\n{output}", encoding="utf-8")
        ok = completed.returncode == 0
        diagnosis = "passed" if ok else f"exit_code={completed.returncode}; {_tail(output)}"
        report.add(section, EvidenceRecord(mode, name, "PASS" if ok else "FAIL", diagnosis, [artifact.relative_to(ROOT).as_posix()]))
        return ok
    except FileNotFoundError as exc:
        artifact.write_text(f"$ {display}\n\n{type(exc).__name__}: executable unavailable", encoding="utf-8")
        report.add(section, EvidenceRecord(mode, name, "NOT_RUN", "required executable is unavailable; local gate is incomplete", [artifact.relative_to(ROOT).as_posix()]))
        return False
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        artifact.write_text(f"$ {display}\n\n{output}\n\nTIMEOUT after {timeout}s", encoding="utf-8")
        report.add(section, EvidenceRecord(mode, name, "FAIL", f"bounded timeout after {timeout}s; {_tail(output)}", [artifact.relative_to(ROOT).as_posix()]))
        return False


def _compose_override(output_dir: Path) -> Path:
    env_file = output_dir / "compose.local-validation.env"
    env_file.write_text(
        """ENVIRONMENT=development
LEAD_ENGINE_DEV_AUTH_BYPASS=0
LOCAL_VALIDATION_MODE=1
LOCAL_AUTHENTICATION_FLAG=1
LOCAL_AUTH_EMAIL=local@example.test
LOCAL_AUTH_PASSWORD=synthetic-password
LOCAL_AUTH_SECRET=local-validation-secret-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
LOCAL_AUTH_USER_ID=local-user
LOCAL_AUTH_TENANT_ID=local-tenant
LOCAL_AUTH_RATE_LIMIT=60
REQUIRE_REDIS_FOR_API_READY=1
REQUIRE_WORKER_READY=1
JOB_QUEUE_FAIL_CLOSED=1
PAID_PROVIDER_ISOLATION=1
LEADX_PAID_FALLBACK=off
LEADX_OFFLINE=1
NO_NETWORK=1
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_KEY=
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
SERPER_API_KEY=
DECODO_API_KEY=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_SESSION_TOKEN=
""",
        encoding="utf-8",
    )
    override = output_dir / "compose.local-validation.override.yml"
    override.write_text(
        """services:
  api:
    env_file: []
    environment:
      ENVIRONMENT: development
      LEAD_ENGINE_DEV_AUTH_BYPASS: "0"
      LOCAL_VALIDATION_MODE: "1"
      LOCAL_AUTHENTICATION_FLAG: "1"
      LOCAL_AUTH_EMAIL: local@example.test
      LOCAL_AUTH_PASSWORD: synthetic-password
      LOCAL_AUTH_SECRET: local-validation-secret-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
      LOCAL_AUTH_USER_ID: local-user
      LOCAL_AUTH_TENANT_ID: local-tenant
      LOCAL_AUTH_RATE_LIMIT: "60"
      LOCAL_DURABLE_PATH: /var/lib/leadx/local-durable.sqlite3
      REQUIRE_REDIS_FOR_API_READY: "1"
      REQUIRE_WORKER_READY: "1"
      JOB_QUEUE_FAIL_CLOSED: "1"
      PAID_PROVIDER_ISOLATION: "1"
      SUPABASE_URL: ""
      SUPABASE_ANON_KEY: ""
      SUPABASE_SERVICE_KEY: ""
      STRIPE_SECRET_KEY: ""
      STRIPE_WEBHOOK_SECRET: ""
      SERPER_API_KEY: ""
  worker:
    env_file: []
    environment:
      ENVIRONMENT: development
      LEAD_ENGINE_DEV_AUTH_BYPASS: "0"
      LOCAL_VALIDATION_MODE: "1"
      LOCAL_AUTHENTICATION_FLAG: "1"
      LOCAL_AUTH_EMAIL: local@example.test
      LOCAL_AUTH_PASSWORD: synthetic-password
      LOCAL_AUTH_SECRET: local-validation-secret-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
      LOCAL_AUTH_USER_ID: local-user
      LOCAL_AUTH_TENANT_ID: local-tenant
      LOCAL_AUTH_RATE_LIMIT: "60"
      LOCAL_DURABLE_PATH: /var/lib/leadx/local-durable.sqlite3
      REQUIRE_REDIS_FOR_API_READY: "1"
      REQUIRE_WORKER_READY: "1"
      JOB_QUEUE_FAIL_CLOSED: "1"
      PAID_PROVIDER_ISOLATION: "1"
      SUPABASE_URL: ""
      SUPABASE_ANON_KEY: ""
      SUPABASE_SERVICE_KEY: ""
      STRIPE_SECRET_KEY: ""
      STRIPE_WEBHOOK_SECRET: ""
      SERPER_API_KEY: ""
  portal:
    build:
      args:
        NEXT_PUBLIC_SUPABASE_URL: ""
        NEXT_PUBLIC_SUPABASE_ANON_KEY: ""
        NEXT_PUBLIC_LEADX_LOCAL_AUTH: "1"
    environment:
      NEXT_PUBLIC_SUPABASE_URL: ""
      NEXT_PUBLIC_SUPABASE_ANON_KEY: ""
      NEXT_PUBLIC_LEADX_LOCAL_AUTH: "1"
""",
        encoding="utf-8",
    )
    return override


def _compose_rows(output: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(output)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        rows = []
        for line in output.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows


def compose_gate(report: EvidenceReport, *, output_dir: Path, env: dict[str, str], timeout: int, health_timeout: int) -> bool:
    docker = shutil.which("docker")
    if not docker:
        report.add("local_build", EvidenceRecord("local_build", "Compose config/build/start/health", "NOT_RUN", "Docker executable unavailable; no Compose result is claimed", []))
        return False
    override = _compose_override(output_dir)
    prefix = [docker, "compose", "--env-file", str(output_dir / "compose.local-validation.env"), "--project-name", "leadx-local-validation", "-f", "docker-compose.yml", "-f", str(override)]
    config_ok = run_command(report, section="local_build", mode="local_build", name="compose_config", command=prefix + ["config", "--quiet"], cwd=ROOT, env=env, output_dir=output_dir, timeout=min(timeout, 120))
    build_ok = False
    up_ok = False
    health_ok = False
    try:
        if config_ok:
            build_ok = run_command(report, section="local_build", mode="local_build", name="compose_build", command=prefix + ["build"], cwd=ROOT, env=env, output_dir=output_dir, timeout=timeout)
        if build_ok:
            up_ok = run_command(report, section="local_build", mode="local_build", name="compose_start", command=prefix + ["up", "-d", "--no-build", "--remove-orphans"], cwd=ROOT, env=env, output_dir=output_dir, timeout=min(timeout, 180))
        if not up_ok:
            logs_path = _artifact_name(output_dir, "compose_service_logs")
            logs = subprocess.run(prefix + ["logs", "--no-color"], cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60, check=False)
            logs_path.write_text(logs.stdout or "", encoding="utf-8")
            report.add("local_build", EvidenceRecord("local_build", "Compose service startup diagnostics", "FAIL", f"Compose start failed; {_tail(logs.stdout or '')}", [logs_path.relative_to(ROOT).as_posix()]))
        if up_ok:
            health_log = _artifact_name(output_dir, "compose_health")
            deadline = time.monotonic() + health_timeout
            last = ""
            while time.monotonic() < deadline:
                check = subprocess.run(prefix + ["ps", "--format", "json"], cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20, check=False)
                last = check.stdout or ""
                rows = {str(row.get("Service")): row for row in _compose_rows(last)}
                if all(rows.get(service, {}).get("State") == "running" and rows.get(service, {}).get("Health") == "healthy" for service in CORE_SERVICES):
                    health_ok = True
                    break
                time.sleep(3)
            health_log.write_text(last, encoding="utf-8")
            report.add("local_build", EvidenceRecord("local_build", "Compose health convergence within 120 seconds", "PASS" if health_ok else "FAIL", "all core services healthy" if health_ok else f"core health did not converge within {health_timeout}s; {_tail(last)}", [health_log.relative_to(ROOT).as_posix()]))
    except (subprocess.TimeoutExpired, OSError) as exc:
        report.add("local_build", EvidenceRecord("local_build", "Compose health convergence within 120 seconds", "FAIL", f"health polling failed: {type(exc).__name__}", []))
    finally:
        # The project name isolates this cleanup from unrelated Compose apps.
        subprocess.run(prefix + ["down", "--remove-orphans"], cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60, check=False)
    return config_ok and build_ok and up_ok and health_ok


def add_remote_boundary(report: EvidenceReport) -> None:
    for name in REMOTE_CHECKS:
        lowered = name.lower()
        boundary = (
            "remote database" if "supabase" in lowered else
            "deployed environment" if "deployment" in lowered else
            "live provider"
        )
        report.add("remote_evidence", EvidenceRecord(
            "remote", f"Remote Evidence: {name}", "UNVERIFIED",
            "not run by the local-only gate; no remote credentials, provider calls, credits, or deployment are used",
            [], evidence_boundary=boundary,
        ))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bounded, local-only LeadX release validation gate")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=int, default=900, help="per-command timeout in seconds")
    parser.add_argument("--health-timeout", type=int, default=120, help="Compose health convergence bound")
    parser.add_argument("--skip-compose", action="store_true", help="record Compose as not run; useful only for environments without Docker")
    args = parser.parse_args()
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    report = EvidenceReport()
    env = _local_environment()
    py = sys.executable
    static_commands = [
        ("full_system_static_contracts", [py, "scripts/full_system_test.py", "--skip-pytest"]),
        ("migrations", [py, "scripts/check_migrations.py"]),
        ("rls_coverage", [py, "scripts/check_rls_coverage.py"]),
        ("security_release_gate", [py, "scripts/security_release_gate.py"]),
        ("runtime_schema", [py, "scripts/verify_runtime_schema.py"]),
        ("plan_contract", [py, "scripts/check_plan_contract.py"]),
        ("frontend_accessibility", [py, "scripts/check_frontend_accessibility.py"]),
        ("frontend_performance_contract", [py, "scripts/check_frontend_performance.py"]),
        ("release_audit", [py, "scripts/release_audit.py"]),
    ]
    for name, command in static_commands:
        run_command(report, section="local_unit_property", mode="local_static", name=name, command=command, cwd=ROOT, env=env, output_dir=output_dir, timeout=args.timeout)

    task_validation_files = [
        "tests/test_agent_mission_contracts.py",
        "tests/test_task2_durable_recovery.py",
        "tests/test_task3_discovery_runtime.py",
        "tests/test_task4_approval_scheduling.py",
        "tests/test_task5_security_properties.py",
        "tests/test_task6_realtime.py",
        "tests/test_task7_crm_contracts.py",
        "tests/test_task8_local_auth.py",
        "tests/test_task9_docker_health.py",
        "tests/test_task10_restart_recovery.py",
        "tests/test_task11_evidence.py",
    ]
    unit_env = env.copy()
    # These contract tests use explicit fakes and per-test local-mode toggles.
    # Keep the global paid fallback from changing the free-first property while
    # still running with all remote credentials blank.
    unit_env["LOCAL_VALIDATION_MODE"] = "false"
    unit_env["LEADX_PAID_FALLBACK"] = "auto"
    # Unit/property cases use explicit fakes; do not inherit service readiness
    # requirements that intentionally fail closed when Redis is absent.
    unit_env["REQUIRE_REDIS_FOR_API_READY"] = "0"
    unit_env["REQUIRE_DISTRIBUTED_RATE_LIMIT"] = "0"
    run_command(report, section="local_unit_property", mode="local_unit", name="python_unit_and_property_tests", command=[py, "-m", "pytest", "-q", "-m", "not integration", *task_validation_files], cwd=ROOT, env=unit_env, output_dir=output_dir, timeout=args.timeout)

    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm:
        run_command(report, section="local_build", mode="local_build", name="portal_typecheck", command=[npm, "run", "typecheck"], cwd=FRONT, env=env, output_dir=output_dir, timeout=args.timeout)
        run_command(report, section="local_build", mode="local_build", name="portal_next_build", command=[npm, "run", "build"], cwd=FRONT, env=env, output_dir=output_dir, timeout=args.timeout)
    else:
        report.add("local_build", EvidenceRecord("local_build", "portal typecheck and Next production build", "NOT_RUN", "npm executable unavailable; frontend build is unverified", []))

    playwright = FRONT / "node_modules" / ".bin" / ("playwright.cmd" if os.name == "nt" else "playwright")
    if playwright.exists():
        run_command(report, section="local_browser", mode="local_browser", name="portal_static_browser_contracts", command=[str(playwright), "test", "e2e/portal-contracts.spec.ts", "--config=e2e/playwright.config.ts", "--reporter=line"], cwd=FRONT, env=env, output_dir=output_dir, timeout=min(args.timeout, 300))
    else:
        report.add("local_browser", EvidenceRecord("local_browser", "portal static browser contracts", "NOT_RUN", "checked-in Playwright executable unavailable; browser evidence is unverified", []))

    run_command(report, section="local_synthetic", mode="local_synthetic", name="local_durable_adapter_smoke", command=[py, "scripts/local_durable_smoke.py"], cwd=ROOT, env=env, output_dir=output_dir, timeout=min(args.timeout, 120))
    run_command(report, section="local_synthetic", mode="local_synthetic", name="complete_synthetic_discovery_to_terminal_flow", command=[py, "scripts/synthetic_validation.py"], cwd=ROOT, env=env, output_dir=output_dir, timeout=min(args.timeout, 300))
    run_command(report, section="local_synthetic", mode="local_synthetic", name="restart_reclaim_and_dependency_recovery", command=[py, "scripts/restart_recovery.py"], cwd=ROOT, env=env, output_dir=output_dir, timeout=min(args.timeout, 300))

    if args.skip_compose:
        report.add("local_build", EvidenceRecord("local_build", "Compose config/build/start/health", "NOT_RUN", "explicitly skipped; no Docker result is claimed", []))
    else:
        compose_gate(report, output_dir=output_dir, env=env, timeout=args.timeout, health_timeout=args.health_timeout)

    add_remote_boundary(report)
    json_path, markdown_path = report.write(output_dir)
    local_records = [record for section, records in report.sections.items() for record in records]
    local_ok = bool(local_records) and all(record.result == "PASS" for record in local_records)
    print(json.dumps({"result": "PASS" if local_ok else "FAIL", "evidence_json": str(json_path), "evidence_markdown": str(markdown_path), "local_records": len(local_records), "remote_records": len(report.remote_evidence)}, indent=2))
    return 0 if local_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
