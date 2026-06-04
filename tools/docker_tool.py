"""
Docker Infrastructure Tool
===========================
Provides the agent with the ability to inspect the health and status of
Docker containers running the OpenTelemetry Demo stack.

Design Decision:
    We use the official Docker SDK for Python (`docker` package) instead of
    shelling out to `docker ps`. This gives us structured data (JSON) rather
    than parsing CLI table output, which is more reliable and testable.
"""

import docker
from docker.errors import DockerException


def get_docker_client() -> docker.DockerClient:
    """
    Create and return a Docker client connected to the local Docker daemon.
    Uses the default socket (unix:///var/run/docker.sock on macOS/Linux).
    """
    try:
        client = docker.from_env()
        client.ping()  # Verify connectivity
        return client
    except DockerException as e:
        raise ConnectionError(
            f"Cannot connect to Docker daemon. Is Docker Desktop running? Error: {e}"
        )


def list_containers(name_filter: str = "otel") -> str:
    """
    List all Docker containers whose names contain the given filter string.
    Returns a formatted summary including container name, status, health,
    and any restart count.

    Args:
        name_filter: Substring to match against container names.
                     Defaults to 'otel' to match OpenTelemetry Demo containers.

    Returns:
        A formatted string table of container statuses suitable for LLM consumption.
    """
    try:
        client = get_docker_client()
    except ConnectionError as e:
        return f"ERROR: {e}"

    containers = client.containers.list(all=True)

    # Filter containers by name
    matched = [
        c for c in containers
        if name_filter.lower() in c.name.lower()
    ]

    if not matched:
        return (
            f"No containers found matching '{name_filter}'. "
            f"Total containers on system: {len(containers)}. "
            f"Is the OpenTelemetry Demo stack running?"
        )

    lines = []
    lines.append(f"{'CONTAINER NAME':<45} {'STATUS':<20} {'STATE':<12} {'HEALTH':<12}")
    lines.append("-" * 89)

    for c in sorted(matched, key=lambda x: x.name):
        health = c.attrs.get("State", {}).get("Health", {}).get("Status", "N/A")
        state = c.attrs.get("State", {}).get("Status", "unknown")
        status = c.status  # e.g., "running", "exited"

        lines.append(f"{c.name:<45} {status:<20} {state:<12} {health:<12}")

    return "\n".join(lines)


def get_container_logs(container_name: str, tail: int = 100, since_time: int | None = None) -> str:
    """
    Fetch the last N log lines from a specific Docker container.

    Args:
        container_name: Exact or partial name of the container.
        tail: Number of recent log lines to retrieve (default: 100).
        since_time: Unix timestamp to filter out older logs.

    Returns:
        The container's recent log output as a string.
    """
    try:
        client = get_docker_client()
    except ConnectionError as e:
        return f"ERROR: {e}"

    containers = client.containers.list(all=True)

    # Find container: prefer exact name match, then unambiguous partial match
    target = None

    # 1. Try exact match first (case-insensitive)
    for c in containers:
        if container_name.lower() == c.name.lower():
            target = c
            break

    # 2. Fall back to partial match, but reject ambiguous results
    if not target:
        partial_matches = [
            c for c in containers
            if container_name.lower() in c.name.lower()
        ]
        if len(partial_matches) == 1:
            target = partial_matches[0]
        elif len(partial_matches) > 1:
            names = ", ".join(sorted(c.name for c in partial_matches))
            return (
                f"ERROR: Ambiguous container name '{container_name}' "
                f"matches {len(partial_matches)} containers: {names}. "
                f"Please use a more specific name."
            )

    if not target:
        return f"ERROR: No container found matching '{container_name}'"

    try:
        logs = target.logs(tail=tail, timestamps=True, since=since_time).decode("utf-8", errors="replace")
        return f"=== Logs from {target.name} (last {tail} lines) ===\n{logs}"
    except Exception as e:
        return f"ERROR: Failed to fetch logs from {target.name}: {e}"
