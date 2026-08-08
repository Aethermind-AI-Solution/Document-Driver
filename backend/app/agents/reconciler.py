import json
from .base import PipelineContext, StageResult, timed_stage

_RANK = {"grounded": 3, "unverified": 2, "ungrounded": 1, "absent": 0}


def reconcile(page_results: list[list[dict]], fields_def: list[dict]) -> list[dict]:
    by_name = {f["name"]: f for f in fields_def}
    # index each page's entries by field_name
    per_page = [{e["field_name"]: e for e in page} for page in page_results]
    out = []
    for name, fdef in by_name.items():
        entries = [p[name] for p in per_page if name in p]
        if fdef.get("type") == "array" and fdef.get("columns"):
            rows = []
            for e in entries:                       # page order
                if e.get("field_value"):
                    try:
                        rows.extend(json.loads(e["field_value"]))
                    except (TypeError, ValueError):
                        pass
            best = max(entries, key=lambda e: _RANK.get(e.get("grounded"), 0), default=None)
            out.append({"field_name": name,
                        "field_value": json.dumps(rows) if rows else None,
                        "source_quote": best.get("source_quote") if best else None,
                        "grounded": best.get("grounded") if best else "absent",
                        "confidence": best.get("confidence") if best else 0.55})
        else:
            best = max(entries, key=lambda e: _RANK.get(e.get("grounded"), 0), default=None)
            out.append(best if best else {"field_name": name, "field_value": None,
                                          "source_quote": None, "grounded": "absent",
                                          "confidence": 0.55})
    return out


class ReconcilerAgent:
    name = "Reconciler"

    async def run(self, ctx: PipelineContext) -> StageResult:
        async def body(c: PipelineContext):
            c.fields = reconcile(c.page_results, c.schema["fields"])
        result = await timed_stage(self.name, body, ctx)
        result.detail = f"{len(ctx.fields)} field(s) from {len(ctx.page_results)} page(s)"
        return result
