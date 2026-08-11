import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Awaitable
from .. import config


@dataclass
class StageResult:
    name: str
    status: str          # "ok" | "attention" | "error"
    detail: str
    duration_ms: int


@dataclass
class PipelineContext:
    db: Any
    document: Any
    hint_type: str
    schema: dict | None = None
    pages: list = field(default_factory=list)
    page_results: list = field(default_factory=list)
    fields: list = field(default_factory=list)
    anomalies: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    actor: Any = None
    hints: dict = field(default_factory=dict)


class Agent(Protocol):
    name: str
    async def run(self, ctx: PipelineContext) -> StageResult: ...


async def timed_stage(name: str, body: Callable[[PipelineContext], Awaitable[None]],
                      ctx: PipelineContext) -> StageResult:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(body(ctx), timeout=config.PIPELINE_STAGE_TIMEOUT)
        status, detail = "ok", ""
    except Exception as exc:
        status, detail = "error", f"{type(exc).__name__}: {exc}"
    result = StageResult(name=name, status=status, detail=detail,
                         duration_ms=int((time.perf_counter() - started) * 1000))
    ctx.trace.append(result)
    return result
