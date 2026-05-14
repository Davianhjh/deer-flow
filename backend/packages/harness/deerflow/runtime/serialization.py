"""Canonical serialization for LangChain / LangGraph objects.

Provides a single source of truth for converting LangChain message
objects, Pydantic models, and LangGraph state dicts into plain
JSON-serialisable Python structures.

Consumers: ``deerflow.runtime.runs.worker`` (SSE publishing) and
``app.gateway.routers.threads`` (REST responses).
"""

from __future__ import annotations

from typing import Any


def serialize_lc_object(obj: Any) -> Any:
    """Recursively serialize a LangChain object to a JSON-serialisable dict."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: serialize_lc_object(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize_lc_object(item) for item in obj]
    # Pydantic v2
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    # Pydantic v1 / older objects
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    # Last resort
    try:
        return str(obj)
    except Exception:
        return repr(obj)


def _is_summary_message(msg: Any) -> bool:
    """Return True if *msg* is a HumanMessage whose ``name`` is ``"summary"``.

    These messages carry compressed conversation context produced by
    ``DeerFlowSummarizationMiddleware`` and are intended only as context
    for the model.  They should never be streamed to the client.
    """
    if msg is None:
        return False
    if isinstance(msg, dict):
        return msg.get("name") == "summary" and msg.get("type") == "human"
    if hasattr(msg, "name") and hasattr(msg, "type"):
        return msg.name == "summary" and msg.type == "human"
    return False


def _filter_messages_for_client(messages: Any) -> Any:
    """Remove messages that should not be sent to the client.

    Currently filters:
    - ``name="summary"`` HumanMessages (summarization context)
    """
    if not isinstance(messages, list):
        return messages
    return [m for m in messages if not _is_summary_message(m)]


def serialize_channel_values(channel_values: dict[str, Any]) -> dict[str, Any]:
    """Serialize channel values, stripping internal LangGraph keys
    and messages that are meant only for the model context.

    Internal keys like ``__pregel_*`` and ``__interrupt__`` are removed
    to match what the LangGraph Platform API returns.  Summary messages
    (``name="summary"``) are filtered from the ``messages`` list because
    they carry model-facing context only.
    """
    result: dict[str, Any] = {}
    for key, value in channel_values.items():
        if key.startswith("__pregel_") or key == "__interrupt__":
            continue
        serialized = serialize_lc_object(value)
        if key == "messages":
            result[key] = _filter_messages_for_client(serialized)
        else:
            result[key] = serialized
    return result


def serialize_messages_tuple(obj: Any) -> Any:
    """Serialize a messages-mode tuple ``(chunk, metadata)``."""
    if isinstance(obj, tuple) and len(obj) == 2:
        chunk, metadata = obj
        return [serialize_lc_object(chunk), metadata if isinstance(metadata, dict) else {}]
    return serialize_lc_object(obj)


def serialize(obj: Any, *, mode: str = "") -> Any:
    """Serialize LangChain objects with mode-specific handling.

    * ``messages`` — obj is ``(message_chunk, metadata_dict)``
    * ``values`` — obj is the full state dict; ``__pregel_*`` keys stripped
    * everything else — recursive ``model_dump()`` / ``dict()`` fallback
    """
    if mode == "messages":
        return serialize_messages_tuple(obj)
    if mode == "values":
        return serialize_channel_values(obj) if isinstance(obj, dict) else serialize_lc_object(obj)
    return serialize_lc_object(obj)
