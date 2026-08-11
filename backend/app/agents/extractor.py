import asyncio
import os
import tempfile
from .. import config, services
from .base import PipelineContext, StageResult, timed_stage


class ExtractorAgent:
    name = "Extractor"

    async def run(self, ctx: PipelineContext) -> StageResult:
        fields = ctx.schema["fields"]
        if config.LEARNING_ENABLED:
            ctx.hints = services.get_correction_hints(ctx.db, ctx.document.document_type, fields)
        sem = asyncio.Semaphore(config.PIPELINE_CONCURRENCY)

        def extract_one(page: dict):
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            try:
                tmp.write(page["pdf_bytes"]); tmp.close()
                return services.ai_extract(page["text"], fields, tmp.name, hints=ctx.hints)
            finally:
                os.unlink(tmp.name)

        async def one(page: dict):
            async with sem:
                try:
                    return await asyncio.to_thread(extract_one, page)
                except Exception:
                    return services._ground_fields({}, fields, "")

        async def body(c: PipelineContext):
            c.page_results = list(await asyncio.gather(*[one(p) for p in c.pages]))

        result = await timed_stage(self.name, body, ctx)
        if result.status != "error":
            n = sum(len(v) for v in ctx.hints.values())
            result.detail = f"{len(ctx.pages)} page(s)" + (f", {n} correction hints" if n else "")
        return result
