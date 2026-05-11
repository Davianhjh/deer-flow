"""Tests for event-store-backed message loading in thread state/history endpoints."""

from __future__ import annotations

import uuid

import pytest

from deerflow.runtime.events.store.memory import MemoryRunEventStore


@pytest.fixture()
def event_store():
    return MemoryRunEventStore()


async def _seed_conversation(event_store: MemoryRunEventStore, thread_id: str = "t1"):
    """Seed a realistic multi-turn conversation matching real checkpoint format."""
    # human_message: id is None (same as real data)
    await event_store.put(
        thread_id=thread_id, run_id="r1",
        event_type="human_message", category="message",
        content={
            "type": "human", "id": None,
            "content": [{"type": "text", "text": "Hello"}],
            "additional_kwargs": {}, "response_metadata": {}, "name": None,
        },
    )
    # ai_tool_call: id is set by LLM
    await event_store.put(
        thread_id=thread_id, run_id="r1",
        event_type="ai_tool_call", category="message",
        content={
            "type": "ai", "id": "lc_run--abc123",
            "content": "",
            "tool_calls": [{"name": "search", "args": {"q": "cats"}, "id": "call_1", "type": "tool_call"}],
            "invalid_tool_calls": [],
            "additional_kwargs": {}, "response_metadata": {}, "name": None,
            "usage_metadata": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        },
    )
    # tool_result: id is None (same as real data)
    await event_store.put(
        thread_id=thread_id, run_id="r1",
        event_type="tool_result", category="message",
        content={
            "type": "tool", "id": None,
            "content": "Found 10 results",
            "tool_call_id": "call_1", "name": "search",
            "artifact": None, "status": "success",
            "additional_kwargs": {}, "response_metadata": {},
        },
    )
    # ai_message: id is set by LLM
    await event_store.put(
        thread_id=thread_id, run_id="r1",
        event_type="ai_message", category="message",
        content={
            "type": "ai", "id": "lc_run--def456",
            "content": "I found 10 results about cats.",
            "tool_calls": [], "invalid_tool_calls": [],
            "additional_kwargs": {}, "response_metadata": {"finish_reason": "stop"}, "name": None,
            "usage_metadata": {"input_tokens": 200, "output_tokens": 100, "total_tokens": 300},
        },
    )
    # Also add a trace event — should NOT appear
    await event_store.put(
        thread_id=thread_id, run_id="r1",
        event_type="llm_request", category="trace",
        content={"model": "gpt-4"},
    )


class TestGetEventStoreMessages:
    """Verify event store message extraction with id patching."""

    @pytest.mark.asyncio
    async def test_extracts_all_message_types(self, event_store):
        await _seed_conversation(event_store)
        events = await event_store.list_messages("t1", limit=500)
        messages = [evt["content"] for evt in events if isinstance(evt.get("content"), dict) and "type" in evt["content"]]
        assert len(messages) == 4
        assert [m["type"] for m in messages] == ["human", "ai", "tool", "ai"]

    @pytest.mark.asyncio
    async def test_null_ids_get_patched(self, event_store):
        """Messages with id=None should get deterministic UUIDs."""
        await _seed_conversation(event_store)
        events = await event_store.list_messages("t1", limit=500)
        messages = []
        for evt in events:
            content = evt.get("content")
            if isinstance(content, dict) and "type" in content:
                if content.get("id") is None:
                    content["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"t1:{evt['seq']}"))
                messages.append(content)

        # All messages now have an id
        for m in messages:
            assert m["id"] is not None
            assert isinstance(m["id"], str)
            assert len(m["id"]) > 0

        # AI messages keep their original id
        assert messages[1]["id"] == "lc_run--abc123"
        assert messages[3]["id"] == "lc_run--def456"

        # Human and tool messages get deterministic ids (same input = same output)
        human_id_1 = str(uuid.uuid5(uuid.NAMESPACE_URL, "t1:1"))
        assert messages[0]["id"] == human_id_1

    @pytest.mark.asyncio
    async def test_empty_thread(self, event_store):
        events = await event_store.list_messages("nonexistent", limit=500)
        messages = [evt["content"] for evt in events if isinstance(evt.get("content"), dict)]
        assert messages == []

    @pytest.mark.asyncio
    async def test_tool_call_fields_preserved(self, event_store):
        await _seed_conversation(event_store)
        events = await event_store.list_messages("t1", limit=500)
        messages = [evt["content"] for evt in events if isinstance(evt.get("content"), dict) and "type" in evt["content"]]

        # AI tool_call message
        ai_tc = messages[1]
        assert ai_tc["tool_calls"][0]["name"] == "search"
        assert ai_tc["tool_calls"][0]["id"] == "call_1"

        # Tool result
        tool = messages[2]
        assert tool["tool_call_id"] == "call_1"
        assert tool["status"] == "success"