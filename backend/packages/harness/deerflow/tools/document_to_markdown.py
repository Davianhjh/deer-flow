"""Convert uploaded office documents to markdown files for chat preview."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

_SUPPORTED_DOCUMENT_EXTENSIONS = {".doc", ".docx", ".rtf", ".xls", ".xlsx", ".csv", ".pdf"}
_UPLOADS_VIRTUAL_PREFIX = f"{VIRTUAL_PATH_PREFIX}/uploads"
_OUTPUTS_VIRTUAL_PREFIX = f"{VIRTUAL_PATH_PREFIX}/outputs"


def parse_document_to_markdown_text(document_path: Path) -> str:
    """Parse a document and return markdown text.

    Placeholder implementation. Replace with real parsing logic.
    """
    _ = document_path
    return ""


def _get_file_filename(file_entry: dict[str, Any]) -> str | None:
    for key in ("filename", "name", "original_filename"):
        value = file_entry.get(key)
        if isinstance(value, str) and value and Path(value).name == value:
            return value
    return None


def _build_output_filename(source_filename: str) -> str:
    # Keep the original extension in the output name to avoid collisions across
    # files sharing the same stem (e.g. report.docx and report.pdf).
    return f"{source_filename}.md"


def _build_artifact_url(thread_id: str, virtual_path: str) -> str:
    stripped = virtual_path.lstrip("/")
    return f"/api/threads/{thread_id}/artifacts/{quote(stripped, safe='/')}"


def _enrich_file_entry_with_markdown(
    *,
    file_entry: dict[str, Any],
    thread_id: str,
    source_filename: str,
    output_filename: str,
) -> dict[str, Any]:
    enriched = dict(file_entry)
    markdown_virtual_path = f"{_OUTPUTS_VIRTUAL_PREFIX}/{output_filename}"
    enriched["markdown_file"] = output_filename
    enriched["markdown_virtual_path"] = markdown_virtual_path
    enriched["markdown_path"] = markdown_virtual_path
    enriched["markdown_artifact_url"] = _build_artifact_url(thread_id, markdown_virtual_path)
    # Explicit mapping for UI/history consumers.
    enriched["document_markdown_mapping"] = {
        "source_filename": source_filename,
        "markdown_filename": output_filename,
        "markdown_artifact_url": enriched["markdown_artifact_url"],
    }
    return enriched


async def process_uploaded_documents_to_markdown(
    *,
    thread_id: str,
    messages: list[Any],
    user_id: str | None = None,
) -> None:
    """Process message additional_kwargs.files and attach markdown mappings in-place."""
    if not messages:
        return

    resolved_user_id = user_id if user_id is not None else get_effective_user_id()
    paths = get_paths()
    uploads_dir = paths.sandbox_uploads_dir(thread_id, user_id=resolved_user_id)
    outputs_dir = paths.sandbox_outputs_dir(thread_id, user_id=resolved_user_id)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    for message in messages:
        additional_kwargs = getattr(message, "additional_kwargs", None)
        if not isinstance(additional_kwargs, dict):
            continue
        files = additional_kwargs.get("files")
        if not isinstance(files, list) or not files:
            continue

        updated_files: list[dict[str, Any]] = []
        for file_entry in files:
            if not isinstance(file_entry, dict):
                continue
            source_filename = _get_file_filename(file_entry)
            if not source_filename:
                updated_files.append(dict(file_entry))
                continue
            source_suffix = Path(source_filename).suffix.lower()
            if source_suffix not in _SUPPORTED_DOCUMENT_EXTENSIONS:
                updated_files.append(dict(file_entry))
                continue

            source_path = uploads_dir / source_filename
            if not source_path.is_file():
                logger.debug("Uploaded document not found for markdown conversion: %s", source_path)
                updated_files.append(dict(file_entry))
                continue

            output_filename = _build_output_filename(source_filename)
            output_path = outputs_dir / output_filename
            try:
                markdown_text = await asyncio.to_thread(parse_document_to_markdown_text, source_path)
                output_path.write_text(markdown_text, encoding="utf-8")
                updated_files.append(
                    _enrich_file_entry_with_markdown(
                        file_entry=file_entry,
                        thread_id=thread_id,
                        source_filename=source_filename,
                        output_filename=output_filename,
                    )
                )
            except Exception:
                logger.exception("Failed document->markdown conversion for %s", source_path)
                updated_files.append(dict(file_entry))

        additional_kwargs["files"] = updated_files

