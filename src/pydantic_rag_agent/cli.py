"""Command-line entry point.

Only wiring belongs here. Business logic should live in framework-agnostic
modules so it stays importable and testable without shelling out to the CLI.
"""


def main() -> None:
    """Run the agent.

    Placeholder until the first real ingestion/query flow lands.
    """
    print("Hello from pydantic-rag-agent!")
