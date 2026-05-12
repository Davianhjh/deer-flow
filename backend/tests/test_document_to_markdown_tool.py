from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage

from deerflow.tools.document_to_markdown import process_uploaded_documents_to_markdown


class _TestPaths:
    def __init__(self, uploads_dir: Path, outputs_dir: Path):
        self._uploads_dir = uploads_dir
        self._outputs_dir = outputs_dir

    def sandbox_uploads_dir(self, _thread_id: str, user_id=None) -> Path:
        _ = user_id
        return self._uploads_dir

    def sandbox_outputs_dir(self, _thread_id: str, user_id=None) -> Path:
        _ = user_id
        return self._outputs_dir


@pytest.mark.anyio
async def test_process_uploaded_documents_to_markdown_enriches_supported_file(tmp_path):
    thread_id = "thread1"
    user_id = "user1"
    uploads_dir = tmp_path / "users" / user_id / "threads" / thread_id / "user-data" / "uploads"
    uploads_dir.mkdir(parents=True)
    (uploads_dir / "report.docx").write_bytes(b"docx")

    messages = [HumanMessage(content="convert", additional_kwargs={"files": [{"filename": "report.docx"}]})]

    with (
        patch("deerflow.tools.document_to_markdown.get_effective_user_id", return_value=user_id),
        patch("deerflow.tools.document_to_markdown.get_paths") as get_paths_mock,
        patch("deerflow.tools.document_to_markdown.parse_document_to_markdown_text", return_value="# converted"),
    ):
        get_paths_mock.return_value = _TestPaths(uploads_dir=uploads_dir, outputs_dir=uploads_dir.parent / "outputs")

        await process_uploaded_documents_to_markdown(thread_id=thread_id, messages=messages)

    file_entry = messages[0].additional_kwargs["files"][0]
    assert file_entry["markdown_file"] == "report.docx.md"
    assert file_entry["markdown_virtual_path"] == "/mnt/user-data/outputs/report.docx.md"
    assert file_entry["markdown_artifact_url"].endswith("/api/threads/thread1/artifacts/mnt/user-data/outputs/report.docx.md")
    assert file_entry["document_markdown_mapping"]["source_filename"] == "report.docx"
    assert (uploads_dir.parent / "outputs" / "report.docx.md").read_text(encoding="utf-8") == "# converted"


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["table.csv", "sheet.xlsx", "legacy.xls", "paper.pdf", "notes.rtf", "brief.doc"])
async def test_process_uploaded_documents_to_markdown_supports_required_extensions(tmp_path, name):
    thread_id = "thread1"
    user_id = "user1"
    uploads_dir = tmp_path / "users" / user_id / "threads" / thread_id / "user-data" / "uploads"
    outputs_dir = uploads_dir.parent / "outputs"
    uploads_dir.mkdir(parents=True)
    Path(uploads_dir / name).write_bytes(b"x")

    messages = [HumanMessage(content="convert", additional_kwargs={"files": [{"filename": name}]})]

    with (
        patch("deerflow.tools.document_to_markdown.get_effective_user_id", return_value=user_id),
        patch("deerflow.tools.document_to_markdown.get_paths") as get_paths_mock,
        patch("deerflow.tools.document_to_markdown.parse_document_to_markdown_text", return_value=""),
    ):
        get_paths_mock.return_value = _TestPaths(uploads_dir=uploads_dir, outputs_dir=outputs_dir)
        await process_uploaded_documents_to_markdown(thread_id=thread_id, messages=messages)

    entry = messages[0].additional_kwargs["files"][0]
    assert entry["markdown_file"] == f"{name}.md"
    assert (outputs_dir / f"{name}.md").exists()


@pytest.mark.anyio
async def test_process_uploaded_documents_to_markdown_skips_unsupported_or_missing_files(tmp_path):
    thread_id = "thread1"
    user_id = "user1"
    uploads_dir = tmp_path / "users" / user_id / "threads" / thread_id / "user-data" / "uploads"
    uploads_dir.mkdir(parents=True)
    (uploads_dir / "keep.txt").write_text("x", encoding="utf-8")

    files = [{"filename": "keep.txt"}, {"filename": "missing.pdf"}]
    messages = [HumanMessage(content="convert", additional_kwargs={"files": files})]

    with (
        patch("deerflow.tools.document_to_markdown.get_effective_user_id", return_value=user_id),
        patch("deerflow.tools.document_to_markdown.get_paths") as get_paths_mock,
    ):
        get_paths_mock.return_value = _TestPaths(uploads_dir=uploads_dir, outputs_dir=uploads_dir.parent / "outputs")
        await process_uploaded_documents_to_markdown(thread_id=thread_id, messages=messages)

    updated = messages[0].additional_kwargs["files"]
    assert updated == files
