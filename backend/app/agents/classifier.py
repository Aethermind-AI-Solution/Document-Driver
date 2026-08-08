import json
from .. import config, services
from .base import PipelineContext, StageResult, timed_stage


async def classify_document(text: str, schema_keys: list[str]) -> tuple[str | None, float]:
    """Ask a cheap model which schema key fits. Returns (key, confidence) or (None, 0.0)."""
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI()
        prompt = ("Classify this document into exactly one of these type keys: "
                  f"{', '.join(schema_keys)}. Respond as JSON "
                  '{"key": "<one key>", "confidence": <0..1>}.\n\nDocument:\n' + text[:4000])
        resp = await client.responses.create(
            model=config.CLASSIFIER_MODEL,
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            text={"format": {"type": "json_schema", "name": "classification", "strict": True,
                             "schema": {"type": "object",
                                        "properties": {"key": {"type": "string"},
                                                       "confidence": {"type": "number"}},
                                        "required": ["key", "confidence"],
                                        "additionalProperties": False}}})
        data = json.loads(resp.output_text)
        return data.get("key"), float(data.get("confidence", 0.0))
    except Exception:
        return None, 0.0


class ClassifierAgent:
    name = "Classifier"

    async def run(self, ctx: PipelineContext) -> StageResult:
        async def body(c: PipelineContext):
            schemas = services.available_schemas(c.db)
            keys = [s["key"] for s in schemas]
            text = c.pages[0]["text"] if c.pages else ""
            key, conf = await classify_document(text, keys)
            chosen = key if (key in keys and conf >= 0.5) else (
                c.hint_type if c.hint_type in keys else "invoice")
            if key and key != c.hint_type:
                services.log(c.db, c.document.id, "Classified",
                             f"hint={c.hint_type} chosen={chosen} ({conf:.2f})", actor=c.actor)
            c.document.document_type = chosen
            c.schema = services.schema_for(c.db, chosen)
            c._detail = f"{chosen} ({conf:.2f})"
        result = await timed_stage(self.name, body, ctx)
        if result.status != "error" and getattr(ctx, "_detail", None):
            result.detail = ctx._detail
        return result
