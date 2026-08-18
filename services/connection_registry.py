import asyncio
from typing import Callable, Awaitable, Any

_registry: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}


def register(thread_id: str, send_fn: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
    _registry[thread_id] = send_fn


def unregister(thread_id: str) -> None:
    _registry.pop(thread_id, None)


def get_send_fn(thread_id: str) -> Callable[[dict[str, Any]], Awaitable[None]] | None:
    return _registry.get(thread_id)
