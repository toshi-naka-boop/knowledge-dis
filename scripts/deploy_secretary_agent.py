"""Deploy (or update) the B-stage secretary onto GEAP Agent Runtime
(Vertex AI Agent Engine). design.md §14.7 "パッケージング".

This script only builds and submits the deployment request -- it never
touches gcloud/IAM/networking (that is the caller's responsibility per the
task scope), and it is not invoked automatically by anything in this repo.

Usage:
    python3 scripts/deploy_secretary_agent.py \\
        --project <PROJECT_ID> \\
        --api-base-url https://<cloud-run-service>.a.run.app \\
        [--location asia-northeast1] \\
        [--staging-bucket gs://knowledge-discovery-2026-agent-staging] \\
        [--secret-name demo-api-key] [--secret-version latest] \\
        [--display-name kd-secretary-runtime]

    # Update an existing deployment instead of creating a new one:
    python3 scripts/deploy_secretary_agent.py --update <RESOURCE_ID> \\
        --project <PROJECT_ID> --api-base-url https://...

Project/location/staging-bucket can also be supplied via the environment
variables GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION and
AGENT_STAGING_BUCKET, so this can be re-run without repeating flags.

On success, prints the deployed resource name (the reasoningEngines/<ID>
name to record in README.md / state.json) to stdout.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS_FILE = REPO_ROOT / "scripts" / "requirements-agent.txt"
# Agent Engine tars extra_packages with their given relative path (tar.add(path)),
# so the package must be referenced as "secretary_agent" with cwd=src/; otherwise the
# runtime gets /code/src/secretary_agent and `import secretary_agent` fails at start.
EXTRA_PACKAGE_DIR = "secretary_agent"
SRC_DIR = Path(__file__).resolve().parents[1] / "src"

DEFAULT_LOCATION = "asia-northeast1"
DEFAULT_SECRET_NAME = "demo-api-key"
DEFAULT_SECRET_VERSION = "latest"
DEFAULT_DISPLAY_NAME = "kd-secretary-runtime"


def _load_pinned_requirements() -> list[str]:
    """Reads scripts/requirements-agent.txt as the single source of truth
    for pinned dependencies, so this script and .venv-agent never drift."""
    lines = REQUIREMENTS_FILE.read_text().splitlines()
    return [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        default=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        help="GCP project ID (env: GOOGLE_CLOUD_PROJECT)",
    )
    parser.add_argument(
        "--location",
        default=os.environ.get("GOOGLE_CLOUD_LOCATION", DEFAULT_LOCATION),
        help=f"Agent Engine deployment region (env: GOOGLE_CLOUD_LOCATION, default {DEFAULT_LOCATION})",
    )
    parser.add_argument(
        "--staging-bucket",
        default=os.environ.get("AGENT_STAGING_BUCKET"),
        help="gs:// bucket for staging the deployment (env: AGENT_STAGING_BUCKET)",
    )
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get("KD_API_BASE_URL"),
        help="Cloud Run base URL, becomes the deployed KD_API_BASE_URL env var (env: KD_API_BASE_URL)",
    )
    parser.add_argument(
        "--secret-name",
        default=DEFAULT_SECRET_NAME,
        help=f"Secret Manager secret holding the Cloud Run API key (default: {DEFAULT_SECRET_NAME})",
    )
    parser.add_argument(
        "--secret-version",
        default=DEFAULT_SECRET_VERSION,
        help=f"Secret Manager version (default: {DEFAULT_SECRET_VERSION})",
    )
    parser.add_argument(
        "--display-name",
        default=DEFAULT_DISPLAY_NAME,
        help=f"Display name for the Agent Engine resource (default: {DEFAULT_DISPLAY_NAME})",
    )
    parser.add_argument(
        "--update",
        metavar="RESOURCE_ID",
        default=None,
        help="Update an existing reasoningEngines resource instead of creating a new one",
    )
    parser.add_argument("--cpu", default="4", help="resource_limits cpu (1,2,4,6,8). default 4")
    parser.add_argument("--memory", default="8Gi", help="resource_limits memory, e.g. 8Gi. default 8Gi")
    parser.add_argument("--min-instances", type=int, default=0, help="0 = scale to zero when idle (no standing Agent Compute cost; round-11 B-2). Use 1 on recording day if cold-start latency matters.")
    parser.add_argument("--max-instances", type=int, default=2)
    parser.add_argument(
        "--container-concurrency", type=int, default=4,
        help="Agent Engine container concurrency. Defaults were chosen after observing worker "
             "restart loops (503 Service Unavailable) under the default limits (design v11 B-stage).",
    )
    args = parser.parse_args(argv)

    if not args.project:
        parser.error("--project (or GOOGLE_CLOUD_PROJECT) is required")
    if not args.api_base_url:
        parser.error("--api-base-url (or KD_API_BASE_URL) is required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.chdir(SRC_DIR)  # see EXTRA_PACKAGE_DIR note

    # Imported lazily so `--help` works even without google-adk/vertexai
    # installed (this script's own imports must not break the existing
    # test suite's collection).
    import vertexai
    from vertexai import agent_engines

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from secretary_agent import SecretaryApp

    vertexai.init(
        project=args.project,
        location=args.location,
        staging_bucket=args.staging_bucket,
    )

    env_vars: dict[str, object] = {
        "KD_API_BASE_URL": args.api_base_url,
        "KD_API_KEY": {"secret": args.secret_name, "version": args.secret_version},
    }
    requirements = _load_pinned_requirements()

    if args.update:
        resource = agent_engines.update(
            resource_name=args.update,
            agent_engine=SecretaryApp(),
            requirements=requirements,
            extra_packages=[EXTRA_PACKAGE_DIR],
            env_vars=env_vars,
            display_name=args.display_name,
            resource_limits={"cpu": args.cpu, "memory": args.memory},
            min_instances=args.min_instances,
            max_instances=args.max_instances,
            container_concurrency=args.container_concurrency,
        )
    else:
        resource = agent_engines.create(
            SecretaryApp(),
            requirements=requirements,
            extra_packages=[EXTRA_PACKAGE_DIR],
            env_vars=env_vars,
            display_name=args.display_name,
            resource_limits={"cpu": args.cpu, "memory": args.memory},
            min_instances=args.min_instances,
            max_instances=args.max_instances,
            container_concurrency=args.container_concurrency,
        )

    print(resource.resource_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
