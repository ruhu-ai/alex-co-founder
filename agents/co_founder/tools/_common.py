"""Shared tool helpers."""

import asyncio

import nest_asyncio


def run(coro):
    """Run an async service-layer call from ADK's tool context (loop may or may
    not already be running depending on the surface)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    nest_asyncio.apply()
    return loop.run_until_complete(coro)
