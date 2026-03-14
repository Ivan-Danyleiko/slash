from __future__ import annotations

from contextlib import contextmanager, nullcontext
from typing import Generator
from uuid import uuid4


def _otel_trace_id() -> str | None:
    try:
        from opentelemetry import trace as otel_trace  # type: ignore
    except Exception:
        return None
    span = otel_trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if not ctx:
        return None
    if not getattr(ctx, "is_valid", False):
        return None
    return f"{int(ctx.trace_id):032x}"


@contextmanager
def stage7_span(name: str) -> Generator[None, None, None]:
    try:
        from opentelemetry import trace as otel_trace  # type: ignore
    except Exception:
        with nullcontext():
            yield
        return
    tracer = otel_trace.get_tracer("prediction_market_scanner.stage7")
    with tracer.start_as_current_span(name):
        yield


def stage7_trace_id_fallback() -> str:
    trace_id = _otel_trace_id()
    if trace_id:
        return trace_id
    return str(uuid4())

