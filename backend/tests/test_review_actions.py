from app.services import resolve_review_action


def test_approve():
    assert resolve_review_action("approve", None) == {
        "status": "approved",
        "review_required": False,
        "log_action": "Approved",
        "log_details": "Human review completed",
    }


def test_reject_with_reason():
    out = resolve_review_action("reject", "GST illegible")
    assert out["status"] == "rejected"
    assert out["review_required"] is False
    assert out["log_action"] == "Rejected"
    assert out["log_details"] == "GST illegible"


def test_reject_without_reason_uses_default():
    assert resolve_review_action("reject", None)["log_details"] == "No reason given"
    assert resolve_review_action("reject", "")["log_details"] == "No reason given"


def test_save_leaves_status_unchanged():
    out = resolve_review_action("save", None)
    assert out["status"] is None
    assert out["review_required"] is None
    assert out["log_action"] == "Edited"
    assert out["log_details"] == "Review values updated"


def test_save_with_reason():
    assert resolve_review_action("save", "fixed a typo")["log_details"] == "fixed a typo"


def test_unknown_action_defaults_to_save():
    out = resolve_review_action("bogus", None)
    assert out["status"] is None
    assert out["log_action"] == "Edited"


def test_reject_with_whitespace_reason_uses_default():
    assert resolve_review_action("reject", "   ")["log_details"] == "No reason given"


def test_save_with_whitespace_reason_uses_default():
    assert resolve_review_action("save", "  \n ")["log_details"] == "Review values updated"


def test_reason_is_trimmed():
    assert resolve_review_action("reject", "  blurry scan  ")["log_details"] == "blurry scan"
