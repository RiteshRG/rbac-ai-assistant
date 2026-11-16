"""Interactive, standalone test console for the read-only HR CSV agent."""

from __future__ import annotations

import logging

from hr_csv_agent import create_hr_csv_graph, initial_state, operation_summary
from llm_config import create_chat_model

PROFILE_CHOICES = ("Engineering", "Finance", "General", "HR", "Marketing", "All")


def ask_for_profile() -> str:
    """Collect a simulated profile so HR authorization can be tested locally."""
    allowed = ", ".join(PROFILE_CHOICES)
    while True:
        profile = input(f"Select a simulated profile ({allowed}): ").strip()
        match = next(
            (choice for choice in PROFILE_CHOICES if choice.casefold() == profile.casefold()),
            None,
        )
        if match:
            return match
        print(f"Choose one of: {allowed}")


def main() -> int:
    """Run multiple question/answer checks without changing the CSV file."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    print("HR CSV Agent - standalone development test")
    print("The selected profile is simulated, not authenticated.")
    print("Only HR may query this CSV. The app is read-only; type 'quit' to exit.")

    profile = ask_for_profile()
    try:
        # Reuse one selected provider model across questions in this test session.
        graph = create_hr_csv_graph(llm=create_chat_model())
    except ValueError as exc:
        print(f"Configuration error: {exc}")
        return 1

    while True:
        question = input("\nAsk an HR CSV question (or 'quit'): ").strip()
        if question.casefold() in {"quit", "exit"}:
            print("Exiting the HR CSV test.")
            return 0
        if not question:
            print("Enter a non-empty question.")
            continue

        print("Checking access and planning the read-only CSV query...")
        try:
            result = None
            last_status = None
            for current_state in graph.stream(
                initial_state(profile, question),
                stream_mode="values",
            ):
                result = current_state
                status = current_state["status"]
                if status != last_status:
                    if status == "planned":
                        print("Query plan received; validating supported fields and operation...")
                    elif status == "plan_valid":
                        print("Plan validated; reading matching CSV rows (read-only)...")
                    elif status == "data_ready":
                        print("CSV data retrieved; asking the model to formulate the answer...")
                    elif status == "ok":
                        print("Answer generated.")
                    last_status = status
            if result is None:
                print("The HR CSV workflow returned no result.")
                continue
        except Exception:
            print("The HR CSV workflow failed unexpectedly. No file changes were made.")
            continue

        print(f"\n{result['answer']}")
        if result.get("error"):
            print(result["error"])
        print(operation_summary(result))
        if result["status"] in {
            "denied",
            "rejected",
            "error",
            "needs_clarification",
            "out_of_scope",
        }:
            print(f"Status: {result['status']}")


if __name__ == "__main__":
    raise SystemExit(main())
