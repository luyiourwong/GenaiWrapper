# Guides for AI Agents and Vibecoding

Better read [README.md](README.md) first for instructions on how to run the project in development mode.

## Execution & Environment Management

- Virtual Environment: Always prefer using executables from the `.venv` virtual environment under the project directory (i.e., `.venv\\Scripts\\python.exe`).
- Package Management: When managing packages, prefer using the `uv` command over global `pip` or `python`. Always add dependencies to `pyproject.toml` first, then run `uv sync` instead of using `uv add`.

## Project Structure & Modularity

- Distributed Structure: Prefer structuring code into distributed folders or modules, avoiding concentrating large amounts of code in a single file.
  - Example: Use a distributed structure such as `config/manager.py` (logic handling) paired with `config/models.py` (Pydantic BaseModel data definitions), rather than consolidating everything into a single `config.py`.

## Types & Data Structures

- Explicit Types: Always define type hints in detail. Every method's inputs and outputs must have type definitions.
- Data Structures: Prefer using Pydantic's `BaseModel` to define data structures; avoid passing complex data using raw `dict` wherever possible.

## Code Style & Formatting

- Frequent Logging: Add `logger` statements at I/O entry/exit points, API call sites, and major operations. Debug-level logging for non-critical items is also encouraged.
- Linter & Formatter: When writing or modifying Python code, use `ruff` for both linting and formatting (`ruff check . --fix & ruff format .`).

---

## External Resources
Model Context Protocol (MCP) servers provide tools to access external resources.
You can use DeepWiki MCP to ask questions about newest libraries.
- google-genai: `"repoName": "googleapis/python-genai"`