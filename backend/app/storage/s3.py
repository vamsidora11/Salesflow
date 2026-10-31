from pathlib import Path
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import Settings


class ObjectStore(Protocol):
    def ensure_bucket(self) -> None: ...
    def upload_file(self, path: Path, key: str, content_type: str) -> None: ...
    def delete(self, key: str) -> None: ...
    def presigned_get_url(self, key: str, expires_seconds: int, filename: str) -> str: ...


class S3ObjectStore:
    """MinIO locally, AWS S3 in production. Presigned URLs are never logged."""

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        config = Config(signature_version="s3v4", s3={"addressing_style": "path"})
        common: dict[str, Any] = {
            "aws_access_key_id": settings.s3_access_key,
            "aws_secret_access_key": settings.s3_secret_key,
            "region_name": settings.s3_region,
            "config": config,
        }
        self._client = boto3.client("s3", endpoint_url=settings.s3_endpoint_url, **common)
        # Signing needs the host the *browser* will call (e.g. localhost:9000, not minio:9000).
        public = settings.s3_public_endpoint_url or settings.s3_endpoint_url
        self._signer = boto3.client("s3", endpoint_url=public, **common)

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self._bucket)

    def upload_file(self, path: Path, key: str, content_type: str) -> None:
        self._client.upload_file(
            str(path), self._bucket, key, ExtraArgs={"ContentType": content_type}
        )

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def presigned_get_url(self, key: str, expires_seconds: int, filename: str) -> str:
        url: str = self._signer.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=expires_seconds,
        )
        return url
