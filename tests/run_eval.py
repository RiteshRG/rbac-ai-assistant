"""Run the Phase 1 HR CSV questions against the configured live model."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from hr_csv.agent import create_hr_csv_graph, initial_state  # noqa: E402
from llm_config import create_chat_model  # noqa: E402


def load_questions(path: Path) -> list[tuple[int, str, str]]:
    """Parse numbered evaluation questions and expected answers from the evaluation text file.

        Parameters:
            path (`Path`): the path input.

        Returns:
            The resulting value (`list[tuple[int, str, str]]`).

        Called by:
            main.
    """
    questions: list[tuple[int, str, str]] = []
    current_number: int | None = None
    current_question: str | None = None

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Q") and ": " in line:
            question_number, question = line.split(": ", maxsplit=1)
            current_number = int(question_number[1:])
            current_question = question
        elif line.startswith("ACTUAL: ") and current_number is not None:
            if current_question is None:
                raise ValueError(f"Question Q{current_number} is missing its text.")
            questions.append(
                (current_number, current_question, line.removeprefix("ACTUAL: "))
            )
            current_number = None
            current_question = None

    if current_number is not None or current_question is not None:
        raise ValueError("The evaluation file ends with an incomplete question.")
    if not questions:
        raise ValueError("No evaluation questions were found.")
    return questions


def main() -> int:
    """Run the command-line workflow and return its process exit status.

        Parameters:
            None.

        Returns:
            The resulting value (`int`).

        Called by:
            the `python tests/run_eval.py` CLI invocation (or direct module execution).
    """
    questions = load_questions(Path(__file__).with_name("eval_questions.txt"))
    graph = create_hr_csv_graph(llm=create_chat_model())
    output_blocks: list[str] = []

    for number, question, actual in questions:
        print(f"Running Q{number}/{len(questions)}...")
        try:
            result = graph.invoke(initial_state("HR", question))
            generated = result.get("error") or result.get("answer") or "No answer returned."
        except Exception as exc:
            generated = f"Agent execution failed: {type(exc).__name__}: {exc}"

        output_blocks.append(
            f"Q{number}: {question}\n"
            f"GENERATED ANSWER: {generated}\n"
            f"ACTUAL ANSWER: {actual}\n"
            "----------------------------------------"
        )

    output_path = Path(__file__).with_name("eval_results.txt")
    output_path.write_text("\n".join(output_blocks) + "\n", encoding="utf-8")
    print(f"Evaluation results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
