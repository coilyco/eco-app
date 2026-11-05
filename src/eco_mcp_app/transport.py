"""Which door a tool call came in by.

The MCP tool path and the REST path share one handler per tool. A model reading a tool
result needs a small one (COI-3373), and the SPA reading the same route needs the whole
board, so the handler asks here who is calling. REST is the default.
"""

from __future__ import annotations

from contextvars import ContextVar

MODEL_CALL: ContextVar[bool] = ContextVar("model_call", default=False)
