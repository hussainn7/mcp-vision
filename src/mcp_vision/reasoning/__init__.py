"""General reasoning harness for MCP-Vision.

The model owns judgment; the runtime owns reality. Every module here is a
decision-making / state layer. Nothing in this package executes a real action
on its own — execution stays with the existing runtimes (browser / desktop) and
their governors.
"""
from __future__ import annotations

from mcp_vision.reasoning.schemas import (
    AgentState,
    MemoryBank,
    ConsequenceLevel,
)

__all__ = ["AgentState", "MemoryBank", "ConsequenceLevel"]