from app import services
from app.models import Document

def _fields(vendor, num, total, date):
    return [
        {"field_name": "vendor_name", "field_value": vendor},
        {"field_name": "invoice_number", "field_value": num},
        {"field_name": "total", "field_value": total},
        {"field_name": "invoice_date", "field_value": date},
    ]

def test_fingerprint_stable_and_ignores_case_space():
    a = services.compute_fingerprint(_fields("Acme  Supplies", "INV-1042", "11800.00", "2026-07-01"))
    b = services.compute_fingerprint(_fields("acme supplies", "inv-1042", "11800.00", "2026-07-01"))
    assert a and a == b

def test_fingerprint_none_without_invoice_number():
    assert services.compute_fingerprint(_fields("Acme", None, "1", "2026-07-01")) is None

def test_find_duplicate_matches_same_org(db_session):
    from app.context import set_current_org
    set_current_org(1)
    fp = services.compute_fingerprint(_fields("Acme", "INV-1042", "11800.00", "2026-07-01"))
    first = Document(filename="a.png", document_type="invoice", stored_path="a", org_id=1, fingerprint=fp)
    db_session.add(first); db_session.commit()
    second = Document(filename="b.png", document_type="invoice", stored_path="b", org_id=1, fingerprint=fp)
    db_session.add(second); db_session.commit()
    dup = services.find_duplicate(db_session, second)
    assert dup is not None and dup.id == first.id

def test_find_duplicate_none_when_unique(db_session):
    from app.context import set_current_org
    set_current_org(1)
    doc = Document(filename="c.png", document_type="invoice", stored_path="c", org_id=1,
                   fingerprint=services.compute_fingerprint(_fields("X", "UNIQ-1", "5", "2026-01-01")))
    db_session.add(doc); db_session.commit()
    assert services.find_duplicate(db_session, doc) is None
