"""
Log Query Tool
==============
Provides the agent with the ability to query logs from the OpenTelemetry Demo.

We support two backends:
    1. Loki (HTTP API) — if the demo is running with the Grafana/Loki stack.
    2. Docker container logs — fallback that always works.

Design Decision:
    We default to Docker logs because they require zero additional setup.
    Loki support is provided as an upgrade path for more advanced querying
    (e.g., LogQL filters, time-range queries).
"""

import json
import urllib.request
import urllib.error
from tools.docker_tool import get_container_logs


# ---------------------------------------------------------------------------
# Strategy 1: Docker-based log querying (always available)
# ---------------------------------------------------------------------------

def query_docker_logs(
    service_name: str,
    tail: int = 150,
    error_only: bool = True,
    search_term: str | None = None,
    since_time: int | None = None,
) -> str:
    """
    Fetch logs from a Docker container and optionally filter for error lines
    and/or a specific search term.

    Args:
        service_name: Name (or partial name) of the service container.
        tail: Number of log lines to fetch.
        error_only: If True, only return lines containing error indicators.
        search_term: Optional keyword to filter log lines by (case-insensitive).
        since_time: Unix timestamp to filter out older logs.

    Returns:
        Filtered log output as a string.
    """
    raw_logs = get_container_logs(service_name, tail=tail, since_time=since_time)

    if raw_logs.startswith("ERROR:"):
        return raw_logs

    lines = raw_logs.split("\n")

    # Apply search_term filter first if provided
    if search_term:
        term_lower = search_term.lower()
        lines = [line for line in lines if term_lower in line.lower()]

    # Then apply error-keyword filter if requested
    if error_only:
        error_keywords = ["error", "err", "exception", "fatal", "panic", "fail", "critical"]
        lines = [
            line for line in lines
            if any(kw in line.lower() for kw in error_keywords)
        ]

    if not lines:
        filter_desc = f" matching '{search_term}'" if search_term else ""
        return f"No error lines found{filter_desc} in the last {tail} log lines of '{service_name}'. The service may be healthy."

    filter_desc = f", search_term='{search_term}'" if search_term else ""
    return (
        f"=== Error logs from '{service_name}' ({len(lines)} lines found{filter_desc}) ===\n"
        + "\n".join(lines)
    )


# ---------------------------------------------------------------------------
# Strategy 2: Loki-based log querying (optional, if Loki is running)
# ---------------------------------------------------------------------------

LOKI_BASE_URL = "http://localhost:3100"


def query_loki(
    service_name: str,
    query: str | None = None,
    limit: int = 50,
) -> str:
    """
    Query the Loki HTTP API for log entries.

    Uses LogQL to filter logs by service name and optional search terms.

    Args:
        service_name: The service/container name to filter logs for.
        query: Optional additional filter term (e.g., 'error', 'timeout').
        limit: Max number of log entries to return.

    Returns:
        Formatted log entries from Loki, or an error message.
    """
    # Build LogQL query
    # Format: {container_name=~".*service_name.*"} |~ "(?i)query"
    logql = f'{{container_name=~".*{service_name}.*"}}'
    if query:
        logql += f' |~ "(?i){query}"'

    params = urllib.parse.urlencode({
        "query": logql,
        "limit": str(limit),
        "direction": "backward",  # Most recent first
    })

    url = f"{LOKI_BASE_URL}/loki/api/v1/query_range?{params}"

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        results = data.get("data", {}).get("result", [])
        if not results:
            return f"No logs found in Loki for service '{service_name}' with query '{query or '*'}'."

        lines = []
        for stream in results:
            for ts, line in stream.get("values", []):
                lines.append(line)

        return (
            f"=== Loki logs for '{service_name}' (query: {query or '*'}) ===\n"
            + "\n".join(lines[:limit])
        )

    except urllib.error.URLError:
        return (
            "ERROR: Cannot connect to Loki at " + LOKI_BASE_URL + ". "
            "Loki may not be running. Falling back to Docker logs."
        )
    except Exception as e:
        return f"ERROR: Loki query failed: {e}"


def query_logs(
    service_name: str,
    search_term: str | None = None,
    tail: int = 150,
    use_loki: bool = False,
    since_time: int | None = None,
) -> str:
    """
    Unified log query interface. Tries Loki first if requested, falls back to Docker.

    Args:
        service_name: Name of the service to query logs for.
        search_term: Optional keyword to filter (e.g., 'error', 'timeout').
        tail: Number of lines to fetch (Docker mode).
        use_loki: Whether to attempt Loki querying first.
        since_time: Unix timestamp to filter out older logs.

    Returns:
        Log output as a string.
    """
    if use_loki:
        result = query_loki(service_name, query=search_term)
        if not result.startswith("ERROR:"):
            return result
        # Fall back to Docker
        fallback_note = result + "\n\n--- Falling back to Docker logs ---\n"
    else:
        fallback_note = ""

    docker_result = query_docker_logs(
        service_name,
        tail=tail,
        error_only=True,
        search_term=search_term,
        since_time=since_time,
    )
    return fallback_note + docker_result

