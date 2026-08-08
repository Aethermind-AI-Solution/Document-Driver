import asyncio
from app.agents.base import PipelineContext, StageResult, timed_stage


def test_context_defaults():
    ctx = PipelineContext(db=None, document=None, hint_type="invoice")
    assert ctx.pages == [] and ctx.fields == [] and ctx.anomalies == [] and ctx.trace == []


def test_timed_stage_records_trace():
    ctx = PipelineContext(db=None, document=None, hint_type="invoice")

    async def body(c):
        c.fields.append("x")

    result = asyncio.run(timed_stage("Demo", body, ctx))
    assert result.name == "Demo" and result.status == "ok"
    assert result.duration_ms >= 0
    assert ctx.trace == [result] and ctx.fields == ["x"]


def test_timed_stage_records_error():
    ctx = PipelineContext(db=None, document=None, hint_type="invoice")

    async def boom(c):
        raise ValueError("nope")

    result = asyncio.run(timed_stage("Boom", boom, ctx))
    assert result.status == "error" and "nope" in result.detail
    assert ctx.trace[-1].status == "error"
