"""
Codebase Search Tool
====================
Provides the agent with the ability to search and read source code from the
locally cloned OpenTelemetry Demo repository.

Design Decision:
    We use Python's built-in `os` and `re` modules to walk the filesystem
    and perform regex searches. This avoids external dependencies and gives
    us full control over what files we search (we skip binaries, node_modules,
    vendor directories, etc.).

    An alternative would be to use the GitHub API or a git CLI wrapper,
    but local file search is faster and works offline.
"""

import os
import re
from pathlib import Path


# File extensions we consider "source code" worth searching
SOURCE_EXTENSIONS = {
    ".py", ".go", ".js", ".ts", ".java", ".cs", ".rb", ".rs",
    ".jsx", ".tsx", ".proto", ".yaml", ".yml", ".json", ".toml",
    ".env", ".cfg", ".conf", ".sh", ".bash", ".dockerfile",
    ".xml", ".html", ".css", ".sql", ".graphql", ".gql",
}

# Directories to skip when walking the file tree
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "vendor", "dist",
    "build", ".next", ".venv", "venv", ".idea", ".vscode",
    "target",  # Rust/Java build dirs
}

# Max file size to read (skip very large files)
MAX_FILE_SIZE = 500_000  # 500 KB


def _is_searchable(filepath: str) -> bool:
    """Check if a file should be included in searches."""
    ext = Path(filepath).suffix.lower()
    if ext not in SOURCE_EXTENSIONS:
        return False
    try:
        size = os.path.getsize(filepath)
        return size <= MAX_FILE_SIZE
    except OSError:
        return False


def search_codebase(
    repo_path: str,
    query: str,
    max_results: int = 15,
    case_sensitive: bool = False,
) -> str:
    """
    Search for a string/pattern across all source files in the repository.

    Args:
        repo_path: Absolute path to the cloned repository root.
        query: The search string or regex pattern.
        max_results: Maximum number of matching files/lines to return.
        case_sensitive: Whether the search is case-sensitive.

    Returns:
        A formatted string showing matching files and the lines that matched,
        with surrounding context.
    """
    if not os.path.isdir(repo_path):
        return f"ERROR: Repository path not found: {repo_path}"

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query, flags)
    except re.error:
        # If the query isn't valid regex, escape it and search literally
        pattern = re.compile(re.escape(query), flags)

    results = []

    for root, dirs, files in os.walk(repo_path):
        # Prune directories we don't want to search
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for filename in files:
            filepath = os.path.join(root, filename)

            if not _is_searchable(filepath):
                continue

            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except (OSError, PermissionError):
                continue

            matches_in_file = []
            for i, line in enumerate(lines, start=1):
                if pattern.search(line):
                    # Grab 2 lines of context above and below
                    context_start = max(0, i - 3)
                    context_end = min(len(lines), i + 2)
                    context = "".join(lines[context_start:context_end])
                    matches_in_file.append((i, context.rstrip()))

            if matches_in_file:
                rel_path = os.path.relpath(filepath, repo_path)
                results.append((rel_path, matches_in_file))

                if len(results) >= max_results:
                    break

        if len(results) >= max_results:
            break

    if not results:
        return f"No matches found for '{query}' in {repo_path}"

    output_lines = [f"Found matches for '{query}' in {len(results)} file(s):\n"]
    for rel_path, matches in results:
        output_lines.append(f"--- {rel_path} ---")
        for line_num, context in matches:
            output_lines.append(f"  Line {line_num}:")
            for ctx_line in context.split("\n"):
                output_lines.append(f"    {ctx_line}")
        output_lines.append("")

    return "\n".join(output_lines)


def read_file(repo_path: str, file_path: str) -> str:
    """
    Read the contents of a specific file from the repository.

    Args:
        repo_path: Absolute path to the repository root.
        file_path: Relative path to the file within the repository.

    Returns:
        The file contents with line numbers, or an error message.
    """
    full_path = os.path.join(repo_path, file_path)

    # Security check: ensure we don't escape the repo directory
    real_repo = os.path.realpath(repo_path)
    real_file = os.path.realpath(full_path)
    try:
        common = os.path.commonpath([real_repo, real_file])
    except ValueError:
        return "ERROR: Access denied. File path is outside the repository."
    if common != real_repo:
        return "ERROR: Access denied. File path is outside the repository."

    if not os.path.isfile(full_path):
        return f"ERROR: File not found: {file_path}"

    try:
        size = os.path.getsize(full_path)
        if size > MAX_FILE_SIZE:
            return f"ERROR: File too large ({size} bytes). Max is {MAX_FILE_SIZE} bytes."

        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        numbered = [f"{i:4d} | {line.rstrip()}" for i, line in enumerate(lines, 1)]
        return f"=== {file_path} ({len(lines)} lines) ===\n" + "\n".join(numbered)

    except Exception as e:
        return f"ERROR: Failed to read {file_path}: {e}"


def list_directory(repo_path: str, dir_path: str = ".") -> str:
    """
    List the contents of a directory in the repository.

    Args:
        repo_path: Absolute path to the repository root.
        dir_path: Relative path to the directory (default: root).

    Returns:
        A formatted directory listing.
    """
    full_path = os.path.join(repo_path, dir_path)

    # Security check
    real_repo = os.path.realpath(repo_path)
    real_dir = os.path.realpath(full_path)
    try:
        common = os.path.commonpath([real_repo, real_dir])
    except ValueError:
        return "ERROR: Access denied. Path is outside the repository."
    if common != real_repo:
        return "ERROR: Access denied. Path is outside the repository."

    if not os.path.isdir(full_path):
        return f"ERROR: Directory not found: {dir_path}"

    entries = []
    try:
        for entry in sorted(os.listdir(full_path)):
            if entry in SKIP_DIRS or entry.startswith("."):
                continue
            entry_path = os.path.join(full_path, entry)
            if os.path.isdir(entry_path):
                entries.append(f"  📁 {entry}/")
            else:
                size = os.path.getsize(entry_path)
                entries.append(f"  📄 {entry} ({size:,} bytes)")
    except PermissionError:
        return f"ERROR: Permission denied for {dir_path}"

    header = f"=== Directory: {dir_path} ({len(entries)} items) ==="
    return header + "\n" + "\n".join(entries)
