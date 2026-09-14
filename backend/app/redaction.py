"""PII redaction helpers. A schema field is PII when it carries `"pii": true`.
Masking is display-only — values remain intact in storage/export."""

def pii_field_names(schema_fields: list[dict]) -> set[str]:
    return {f["name"] for f in schema_fields if f.get("pii")}


def mask(value: str | None) -> str:
    return "••••" if value and str(value).strip() else ""
