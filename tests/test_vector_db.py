"""Focused tests for chunking, repeatable ingestion, and RBAC-filtered search."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Sequence

from vector_service.chunking import make_document_chunks
from vector_service.ingestion import (
    discover_markdown_files,
    ingest_all_documents,
    ingest_one_document,
)
from vector_service.retrieval import (
    AuthorizationError,
    DocumentSearchService,
    effective_access_groups,
)


def simple_token_count(text: str) -> int:
    """Use word count for quick tests; ingestion uses the model tokenizer."""
    return len(text.split())


class FakeCollection:
    """In-memory stand-in for the Chroma methods used by these tests."""

    name = "test_documents"

    def __init__(self) -> None:
        self.records: dict[str, tuple[str, dict[str, str | int]]] = {}
        self.last_query: dict[str, object] | None = None

    def get(self, where: dict[str, str], include: list[str]) -> dict[str, Any]:
        if "source_file" in where:
            is_match = lambda metadata: metadata["source_file"] == where["source_file"]
        else:
            allowed = where["access_group"]["$in"]  # type: ignore[index]
            is_match = lambda metadata: metadata["access_group"] in allowed

        matching = [
            (chunk_id, metadata)
            for chunk_id, (_, metadata) in self.records.items()
            if is_match(metadata)
        ]
        response: dict[str, Any] = {"ids": [chunk_id for chunk_id, _ in matching]}
        if "metadatas" in include:
            response["metadatas"] = [metadata for _, metadata in matching]
        return response

    def upsert(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, str | int]],
    ) -> None:
        for chunk_id, document, metadata in zip(ids, documents, metadatas):
            self.records[chunk_id] = (document, metadata)

    def delete(self, ids: list[str]) -> None:
        for chunk_id in ids:
            self.records.pop(chunk_id, None)

    def query(
        self,
        query_texts: list[str],
        n_results: int,
        where: dict[str, object],
        include: list[str],
    ) -> dict[str, list[list[object]]]:
        self.last_query = {
            "query_texts": query_texts,
            "where": where,
            "n_results": n_results,
        }
        allowed_groups = where["access_group"]["$in"]  # type: ignore[index]
        matching = [
            (text, metadata)
            for text, metadata in self.records.values()
            if metadata["access_group"] in allowed_groups
        ][:n_results]
        response: dict[str, Any] = {}
        if "documents" in include:
            response["documents"] = [[text for text, _ in matching]]
        if "metadatas" in include:
            response["metadatas"] = [[metadata for _, metadata in matching]]
        if "distances" in include:
            response["distances"] = [[0.1 for _ in matching]]
        return response


class FakeEmployeeRepository:
    """Return provisioned employee groups for the retrieval authorization tests."""

    def __init__(self, profiles: dict[str, Sequence[str]]) -> None:
        self.profiles = profiles

    def get_access_groups(self, employee_id: str) -> Sequence[str] | None:
        return self.profiles.get(employee_id)


class FailingEmployeeRepository:
    """Simulate an unavailable trusted profile store."""

    def get_access_groups(self, employee_id: str) -> Sequence[str] | None:
        if employee_id == "employee-1":
            raise RuntimeError("Profile store is unavailable.")
        return None


class VectorDatabaseTests(unittest.TestCase):
    """Check core acceptance criteria without downloading or embedding a model."""

    def test_effective_groups_add_general_and_expand_all(self) -> None:
        self.assertEqual(effective_access_groups(["Finance"]), ("Finance", "General"))
        self.assertEqual(
            effective_access_groups(["All"]),
            ("Engineering", "Finance", "General", "HR", "Marketing"),
        )

    def test_invalid_groups_fail_closed(self) -> None:
        for groups in (None, [], ["Unknown"], ["All", "Finance"]):
            with self.subTest(groups=groups), self.assertRaises(AuthorizationError):
                effective_access_groups(groups)

    def test_heading_sections_and_chunk_metadata_are_preserved(self) -> None:
        markdown = (
            "# Finance Summary\n\n"
            "Executive Summary:\n"
            "------------------\n"
            "Revenue grew this year.\n\n"
            "Cash Flow Analysis:\n"
            "-------------------\n"
            "Cash flow remained positive."
        )
        chunks = make_document_chunks(
            markdown,
            Path("finance") / "summary.md",
            Path("."),
            simple_token_count,
        )

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].metadata["access_group"], "Finance")
        self.assertEqual(
            chunks[0].metadata["section_path"],
            "Finance Summary > Executive Summary",
        )
        self.assertEqual(
            chunks[1].metadata["section_path"],
            "Finance Summary > Cash Flow Analysis",
        )
        self.assertEqual(chunks[0].metadata["chunk_index"], 0)

    def test_hr_folder_is_rejected_by_chunking_too(self) -> None:
        with self.assertRaises(ValueError):
            make_document_chunks(
                "# HR Data\n\nEmployee records.",
                Path("hr") / "records.md",
                Path("."),
                simple_token_count,
            )

    def test_ingestion_is_repeatable_and_removes_stale_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_root = Path(temporary_directory)
            source_folder = data_root / "engineering"
            source_folder.mkdir()
            source_file = source_folder / "guide.md"
            source_file.write_text("# Guide\n\nOriginal section content.", encoding="utf-8")
            collection = FakeCollection()

            first_count = ingest_one_document(
                source_file, data_root, collection, simple_token_count
            )
            first_ids = set(collection.records)
            second_count = ingest_one_document(
                source_file, data_root, collection, simple_token_count
            )
            self.assertEqual(first_count, second_count)
            self.assertEqual(first_ids, set(collection.records))

            source_file.write_text("# Guide\n\nUpdated section content.", encoding="utf-8")
            ingest_one_document(source_file, data_root, collection, simple_token_count)
            self.assertEqual(len(collection.records), 1)
            self.assertNotEqual(first_ids, set(collection.records))

    def test_source_discovery_never_reads_hr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_root = Path(temporary_directory)
            for department in ("engineering", "finance", "general", "marketing", "hr"):
                folder = data_root / department
                folder.mkdir()
                (folder / "document.md").write_text("# Test\n\nContent.", encoding="utf-8")
            (data_root / "hr" / "hr_data.csv").write_text("private\n", encoding="utf-8")

            found = discover_markdown_files(data_root)

            self.assertEqual(len(found), 4)
            self.assertTrue(all(path.parent.name != "hr" for path in found))

    def test_ingestion_removes_chunks_for_deleted_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_root = Path(temporary_directory)
            for department in ("engineering", "finance", "general", "marketing"):
                folder = data_root / department
                folder.mkdir()
                (folder / "keep.md").write_text(
                    "# Kept\n\nCurrent content.", encoding="utf-8"
                )
            removed_source = data_root / "finance" / "remove.md"
            removed_source.write_text("# Old\n\nOld content.", encoding="utf-8")
            collection = FakeCollection()

            ingest_all_documents(data_root, collection, simple_token_count)
            removed_ids = {
                chunk_id
                for chunk_id, (_, metadata) in collection.records.items()
                if metadata["source_file"] == "finance/remove.md"
            }
            self.assertTrue(removed_ids)

            removed_source.unlink()
            ingest_all_documents(data_root, collection, simple_token_count)

            self.assertTrue(removed_ids.isdisjoint(collection.records))

    def test_search_filter_comes_from_profile_and_hides_other_groups(self) -> None:
        collection = FakeCollection()
        collection.records["finance"] = (
            "Finance plan",
            {
                "access_group": "Finance",
                "source_file": "finance/plan.md",
                "document_title": "Finance Plan",
                "section_path": "Finance Plan > Overview",
                "chunk_index": 0,
            },
        )
        collection.records["engineering"] = (
            "Engineering plan",
            {
                "access_group": "Engineering",
                "source_file": "engineering/plan.md",
                "document_title": "Engineering Plan",
                "section_path": "Engineering Plan > Overview",
                "chunk_index": 0,
            },
        )
        collection.records["general"] = (
            "General handbook",
            {
                "access_group": "General",
                "source_file": "general/handbook.md",
                "document_title": "Employee Handbook",
                "section_path": "Employee Handbook > Welcome",
                "chunk_index": 0,
            },
        )
        service = DocumentSearchService(
            collection,
            FakeEmployeeRepository({"employee-1": ["Finance"]}),
        )

        results = service.search("employee-1", "plan")

        self.assertEqual(
            {result.access_group for result in results},
            {"Finance", "General"},
        )
        self.assertEqual(
            collection.last_query["where"],  # type: ignore[index]
            {"access_group": {"$in": ["Finance", "General"]}},
        )

    def test_unknown_employee_never_queries_collection(self) -> None:
        collection = FakeCollection()
        service = DocumentSearchService(collection, FakeEmployeeRepository({}))

        with self.assertRaises(AuthorizationError):
            service.search("unknown", "query")

        self.assertIsNone(collection.last_query)

    def test_profile_store_error_is_denied_before_collection_query(self) -> None:
        collection = FakeCollection()
        service = DocumentSearchService(collection, FailingEmployeeRepository())

        with self.assertRaises(AuthorizationError):
            service.search("employee-1", "query")

        self.assertIsNone(collection.last_query)


if __name__ == "__main__":
    unittest.main()
