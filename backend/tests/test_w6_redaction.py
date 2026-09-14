from app import redaction

def test_pii_field_names():
    fields = [{"name": "claimant", "pii": True}, {"name": "total"}, {"name": "ssn", "pii": True}]
    assert redaction.pii_field_names(fields) == {"claimant", "ssn"}

def test_mask():
    assert redaction.mask("John Doe") == "••••"
    assert redaction.mask("") == ""
    assert redaction.mask(None) == ""

def test_schema_pii_flag_passthrough(client, db_session):
    from app.models import SchemaDefinition
    db_session.add(SchemaDefinition(key="claim_x", name="Claim X",
                   fields=[{"name": "claimant", "type": "string", "pii": True}], org_id=1))
    db_session.commit()
    schemas = client.get("/schemas").json()
    claim = next(s for s in schemas if s["key"] == "claim_x")
    assert claim["fields"][0]["pii"] is True
