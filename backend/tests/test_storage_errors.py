import pytest
from botocore.exceptions import ClientError
from app.storage import S3Storage


def _s3_with_failing_put(response: dict) -> S3Storage:
    """Build an S3Storage without touching boto3/network; inject a client whose
    put_object raises a ClientError with the given response."""
    s = S3Storage.__new__(S3Storage)
    s.bucket = "mybucket"

    class _Client:
        def put_object(self, **_kw):
            raise ClientError(response, "PutObject")

    s.client = _Client()
    return s


def test_save_surfaces_http_status_code_and_bucket():
    resp = {"Error": {"Code": "AccessDenied", "Message": "denied"},
            "ResponseMetadata": {"HTTPStatusCode": 403}}
    with pytest.raises(RuntimeError) as ei:
        _s3_with_failing_put(resp).save("k", b"d")
    m = str(ei.value)
    assert "403" in m and "AccessDenied" in m and "mybucket" in m


def test_save_surfaces_status_even_when_error_code_empty():
    # The exact production symptom: empty Code/Message, but a real HTTP status.
    resp = {"Error": {"Code": "", "Message": ""},
            "ResponseMetadata": {"HTTPStatusCode": 403}}
    with pytest.raises(RuntimeError) as ei:
        _s3_with_failing_put(resp).save("k", b"d")
    assert "403" in str(ei.value)
