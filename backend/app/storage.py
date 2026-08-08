from pathlib import Path
from typing import Protocol
from . import config


class Storage(Protocol):
    def save(self, key: str, data: bytes) -> str: ...
    def open(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, base: Path):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)

    def save(self, key: str, data: bytes) -> str:
        (self.base / key).write_bytes(data)
        return key

    def open(self, key: str) -> bytes:
        return (self.base / key).read_bytes()

    def delete(self, key: str) -> None:
        (self.base / key).unlink(missing_ok=True)


class S3Storage:
    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str, region: str = "auto"):
        import boto3
        from botocore.config import Config
        self.bucket = bucket
        # Path-style addressing + explicit region keep SigV4 valid across S3-compatible
        # providers (R2 tolerates "auto"; Supabase/B2 require their real region).
        self.client = boto3.client(
            "s3", endpoint_url=endpoint, region_name=region,
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))

    def save(self, key: str, data: bytes) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return key

    def open(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def get_storage() -> Storage:
    if config.STORAGE_BACKEND == "s3":
        missing = [n for n, v in [("R2_ENDPOINT", config.R2_ENDPOINT),
                                  ("R2_BUCKET", config.R2_BUCKET),
                                  ("R2_ACCESS_KEY_ID", config.R2_ACCESS_KEY_ID),
                                  ("R2_SECRET_ACCESS_KEY", config.R2_SECRET_ACCESS_KEY)] if not v]
        if missing:
            raise RuntimeError(f"STORAGE_BACKEND=s3 requires: {', '.join(missing)}")
        return S3Storage(config.R2_ENDPOINT, config.R2_BUCKET,
                         config.R2_ACCESS_KEY_ID, config.R2_SECRET_ACCESS_KEY,
                         region=config.S3_REGION)
    return LocalStorage(config.UPLOAD_DIR)
