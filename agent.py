"""
SRE Troubleshooting Agent
=========================
An autonomous Site Reliability Engineering agent built with Pydantic AI.
It investigates production incidents by querying Docker infrastructure,
application logs, and source code to find the root cause of failures.

Architecture:
    - Agent: Pydantic AI agent with a system prompt defining its SRE role.
    - Tools: Three capability modules (docker, logs, codebase) registered
      via @agent.tool decorators.
    - Dependencies: A simple dataclass holding runtime configuration like
      the path to the OpenTelemetry Demo repository.
    - Output: Structured diagnosis using a Pydantic model.

Usage:
    python agent.py
    python agent.py "There are errors in the product catalog service"
"""

import sys
import os
from dataclasses import dataclass

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from tools.docker_tool import list_containers, get_container_logs
from tools.log_tool import query_logs
from tools.codebase_tool import search_codebase, read_file, list_directory

# Load environment variables from .env file
load_dotenv()


# ---------------------------------------------------------------------------
# 1. DEPENDENCIES — data the agent needs at runtime
# ---------------------------------------------------------------------------

@dataclass
class AgentDependencies:
    """
    Runtime dependencies injected into every tool call via RunContext.

    Attributes:
        repo_path: Absolute path to the cloned opentelemetry-demo repository.
        start_time: Unix timestamp of when the agent was started, to filter out old logs.
    """
    repo_path: str
    start_time: int


# ---------------------------------------------------------------------------
# 2. STRUCTURED OUTPUT — the agent's diagnosis report
# ---------------------------------------------------------------------------

class DiagnosisReport(BaseModel):
    """
    Structured output model for the agent's root cause analysis.
    Pydantic AI enforces that the agent returns data in this exact shape.
    """
    summary: str = Field(
        description="A concise 1-2 sentence summary of the issue."
    )
    affected_service: str = Field(
        description="The name of the service experiencing the failure."
    )
    error_message: str = Field(
        description="The key error message found in the logs."
    )
    root_cause: str = Field(
        description="The identified root cause of the issue."
    )
    evidence: list[str] = Field(
        description="List of evidence items that support the diagnosis (log lines, code references, etc)."
    )
    suggested_fix: str = Field(
        description="Recommended action to resolve the issue."
    )
    confidence: str = Field(
        description="Confidence level: HIGH, MEDIUM, or LOW."
    )


# ---------------------------------------------------------------------------
# 3. AGENT DEFINITION
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert Site Reliability Engineer (SRE) agent specializing in \
troubleshooting architectures. Your mission is to find the \
root cause of production incidents through a rigorous review.

## Your Investigation Methodology
Follow this structured approach for every investigation:
1. **Triage** — Investigate the current system state using your available tools.
2. **Gather Evidence** — Pull and cross-reference metrics and error logs to isolate the failing microservice.
3. **Correlate** — Map any symptoms or errors to specific lines of code, active system configurations, or systemic issues.
4. **Diagnose** — Conduct a rigorous review of all findings to identify the root cause with supporting evidence.

## Rules
- If multiple separate issues or failure states are active, you MUST report on ALL of them. Do not stop after finding just one.
- Not all system failures produce explicit error logs (e.g., resource exhaustion, silent drops). Be sure to check all available system states.
- If a specific tool reports that a systemic condition is "off" or "inactive", treat that as the absolute ground truth over historical log entries.
- Search the codebase to understand WHERE and WHY an error occurs.
- Be specific in your diagnosis — cite exact log lines, file paths, and line numbers.
- If you cannot determine the root cause with HIGH confidence, say so honestly.
"""

agent = Agent(
    "google:gemini-3.1-pro-preview",
    deps_type=AgentDependencies,
    output_type=DiagnosisReport,
    system_prompt=SYSTEM_PROMPT,
)


# ---------------------------------------------------------------------------
# 4. TOOL REGISTRATIONS — connecting capabilities to the agent
# ---------------------------------------------------------------------------

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align

console = Console()


@agent.tool
def check_container_status(
    ctx: RunContext[AgentDependencies],
    name_filter: str = "",
) -> str:
    """
    Check the status of Docker containers in the OpenTelemetry Demo stack.
    Use this FIRST to understand overall system health and identify failing services.

    Args:
        name_filter: Filter containers by name substring (default: 'otel').
    """
    console.print("[bold blue]🔍 [Tool] Checking Docker container health and statuses...[/]")
    return list_containers(name_filter=name_filter)


@agent.tool
def fetch_container_logs(
    ctx: RunContext[AgentDependencies],
    container_name: str,
    tail: int = 100,
) -> str:
    """
    Fetch raw logs from a specific Docker container.
    Use this to see ALL logs (not just errors) from a particular service.

    Args:
        container_name: Full or partial name of the container.
        tail: Number of recent log lines to retrieve.
    """
    console.print(f"[bold cyan]📋 [Tool] Fetching raw logs from container: [underline]{container_name}[/] (tail={tail})...[/]")
    return get_container_logs(container_name, tail=tail, since_time=ctx.deps.start_time)


@agent.tool
def fetch_error_logs(
    ctx: RunContext[AgentDependencies],
    service_name: str,
    search_term: str | None = None,
    tail: int = 200,
) -> str:
    """
    Fetch and filter error logs from a service. Automatically filters for
    lines containing 'error', 'exception', 'fatal', etc.
    Use this after identifying a problematic service to find specific error messages.

    Args:
        service_name: Name of the service/container to query.
        search_term: Optional additional keyword to search for.
        tail: Number of log lines to scan.
    """
    kw_str = f" containing '{search_term}'" if search_term else ""
    console.print(f"[bold yellow]⚠️  [Tool] Querying error logs for service: [underline]{service_name}[/]{kw_str} (tail={tail})...[/]")
    return query_logs(service_name, search_term=search_term, tail=tail, since_time=ctx.deps.start_time)


@agent.tool
def search_code(
    ctx: RunContext[AgentDependencies],
    query: str,
    max_results: int = 10,
) -> str:
    """
    Search the OpenTelemetry Demo source code for a given string or pattern.
    Use this to find where specific error messages, feature flags, or
    configuration values are defined in the codebase.

    Args:
        query: The search string or regex pattern to find in the code.
        max_results: Maximum number of file matches to return.
    """
    console.print(f"[bold magenta]🔎 [Tool] Searching codebase for pattern: '[underline]{query}[/]' (max={max_results})...[/]")
    return search_codebase(ctx.deps.repo_path, query, max_results=max_results)


@agent.tool
def read_source_file(
    ctx: RunContext[AgentDependencies],
    file_path: str,
) -> str:
    """
    Read the full contents of a specific source file from the repository.
    Use this to examine the code around a matched line and understand context.

    Args:
        file_path: Relative path to the file within the repository
                   (e.g., 'src/productcatalogservice/main.go').
    """
    console.print(f"[bold green]📄 [Tool] Reading source file: [underline]{file_path}[/]...[/]")
    return read_file(ctx.deps.repo_path, file_path)


@agent.tool
def browse_directory(
    ctx: RunContext[AgentDependencies],
    dir_path: str = ".",
) -> str:
    """
    List the contents of a directory in the repository.
    Use this to explore the project structure and find relevant files.

    Args:
        dir_path: Relative path to the directory (default: repository root).
    """
    console.print(f"[bold blue]📁 [Tool] Browsing directory structure: [underline]{dir_path}[/]...[/]")
    return list_directory(ctx.deps.repo_path, dir_path)



# ---------------------------------------------------------------------------
# 5. MAIN ENTRY POINT
# ---------------------------------------------------------------------------

def main():
    """Run the SRE agent with a user-provided prompt or default prompt."""
    import time
    
    repo_path = os.getenv("OTEL_DEMO_REPO_PATH", "")

    if not repo_path or not os.path.isdir(repo_path):
        console.print("[bold red]⚠️  OTEL_DEMO_REPO_PATH is not set or directory doesn't exist.[/]")
        console.print("   Set it in your .env file to point to your cloned opentelemetry-demo repo.")
        console.print("   The agent will still work for Docker/Log tools but code search will fail.\n")

    deps = AgentDependencies(repo_path=repo_path, start_time=int(time.time()))

    # Accept prompt from command line or use a default
    if len(sys.argv) > 1:
        user_prompt = " ".join(sys.argv[1:])
    else:
        user_prompt = (
            "There are errors being reported in the system. "
            "Please investigate and find the root cause."
        )

    # Print Premium Header
    console.print(Panel(
        Align.center("[bold cyan]🤖 SRE TROUBLESHOOTING AGENT (Pydantic AI)[/]\n[dim]Autonomous Incident Diagnosis Stack[/]"),
        border_style="cyan"
    ))
    console.print(f"[bold yellow]📋 Incident Prompt:[/] {user_prompt}")
    console.print(f"[bold green]📂 Local Repository:[/] [underline]{repo_path or '(not configured)'}[/]")
    console.print(f"[bold blue]⚡ Model:[/]             [bold white]gemini-3.1-pro-preview[/]")
    console.print(f"[bold magenta]🛠️  Capabilities:[/]     [white]Docker Status, Container Logs, Unified Search, Regex Codebase Search, Flagd OFREP API[/]")
    console.print("\n" + "─" * 80 + "\n")

    # Start investigation with dynamic spinner
    with console.status("[bold green]Agent conducting investigations across containers, logs and codebase...[/]", spinner="dots"):
        import time
        # Give the load generator a few seconds to populate fresh logs 
        # since we are filtering out everything before start_time
        time.sleep(3) 
        result = agent.run_sync(user_prompt, deps=deps)
        report: DiagnosisReport = result.output

    # Print the structured diagnosis using gorgeous Rich layout
    console.print("\n" + "─" * 80 + "\n")
    
    # Confidence Badge
    conf_color = "green" if report.confidence.upper() == "HIGH" else "yellow" if report.confidence.upper() == "MEDIUM" else "red"
    conf_badge = f"[bold white on {conf_color}] {report.confidence} CONFIDENCE [/]"

    # Main Diagnosis Summary Panel
    console.print(Panel(
        f"[bold white]Summary:[/]\n{report.summary}\n\n"
        f"[bold white]Root Cause:[/]\n{report.root_cause}",
        title=f"📊 [bold]DIAGNOSIS REPORT[/] — {conf_badge}",
        border_style="cyan",
        expand=False
    ))

    # Details Table
    details_table = Table(title="🔍 Incident Metadata & Recommendation", show_header=True, header_style="bold magenta", expand=True)
    details_table.add_column("Affected Service", style="bold cyan")
    details_table.add_column("Key Error Caught", style="bold red")
    details_table.add_column("Suggested Fix", style="bold green")
    
    details_table.add_row(
        report.affected_service,
        report.error_message,
        report.suggested_fix
    )
    console.print(details_table)

    # Evidence List
    evidence_table = Table(title="📎 Evidence Gathered", show_header=True, header_style="bold yellow", expand=True)
    evidence_table.add_column("Idx", style="dim", width=4)
    evidence_table.add_column("Evidence Description")
    
    for i, item in enumerate(report.evidence, 1):
        evidence_table.add_row(str(i), item)
        
    console.print(evidence_table)
    console.print("\n[bold cyan]🏁 Diagnosis Complete.[/]\n")


if __name__ == "__main__":
    main()
