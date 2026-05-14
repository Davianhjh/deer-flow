"""Aliyun OSS (Object Storage Service) upload integration.

Provides a lightweight wrapper around ``alibabacloud-oss-v2`` for
uploading local files to an OSS bucket and generating presigned or
public download URLs.

Configuration is read from environment variables (or a dotenv file):

    OSS_ACCESS_KEY_ID      — Aliyun AccessKey ID
    OSS_ACCESS_KEY_SECRET  — Aliyun AccessKey Secret
    OSS_ENDPOINT           — OSS endpoint, e.g. ``oss-cn-beijing.aliyuncs.com``
    OSS_BUCKET             — Target bucket name
    OSS_REGION             — (optional) bucket region, defaults to ``oss-cn-beijing``
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import oss2  # type: ignore[import-untyped]
    from oss2 import Auth, Bucket
except ImportError:  # pragma: no cover
    oss2 = None  # type: ignore[assignment]
    Auth = None  # type: ignore[assignment,misc]
    Bucket = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------


def _get_required(key: str) -> str:
    """Read an env var, raising a clear error when missing."""
    import os

    value = os.getenv(key)
    if not value:
        raise RuntimeError(
            f"OSS environment variable {key} is not set. "
            f"Please configure it in .env or your shell environment."
        )
    return value


def _get_optional(key: str, default: str = "") -> str:
    import os

    return os.getenv(key, default)


# ---------------------------------------------------------------------------
# OSS client factory (lazy singleton)
# ---------------------------------------------------------------------------

_oss_bucket: Bucket | None = None


def _ensure_bucket() -> Bucket:
    """Return a cached OSS Bucket instance, creating it on first call."""
    global _oss_bucket
    if _oss_bucket is not None:
        return _oss_bucket

    if oss2 is None:
        raise RuntimeError(
            "alibabacloud-oss-v2 is not installed. "
            "Run: uv sync  (or  pip install alibabacloud-oss-v2)"
        )

    access_key_id = _get_required("OSS_ACCESS_KEY_ID")
    access_key_secret = _get_required("OSS_ACCESS_KEY_SECRET")
    endpoint = _get_required("OSS_ENDPOINT")
    bucket_name = _get_required("OSS_BUCKET")

    auth = Auth(access_key_id, access_key_secret)
    _oss_bucket = Bucket(auth, endpoint, bucket_name)
    logger.info(
        "OSS bucket connected: %s (endpoint=%s)", bucket_name, endpoint
    )
    return _oss_bucket


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def upload_file_without_sign(
    local_path: str | Path,
    oss_key: str | None = None,
    *,
    content_type: str | None = None,
    metadata: dict[str, str] | None = None,
    progress_callback: Any = None,
) -> str:
    """Upload a local file to OSS.

    Args:
        local_path: Absolute path to the local file.
        oss_key: Object key (path) in the OSS bucket, e.g.
                 ``"threads/abc123/outputs/report.pdf"``.
                 When ``None``, a default key is generated:
                 ``deer-flow/{yyyyMMddHHmmss}/{file_name}``.
        content_type: Optional MIME type.  Guessed from the file extension
                      when omitted.
        metadata: Optional user-defined metadata dict.
        progress_callback: Optional ``oss2.models.ProgressCallback``.

    Returns:
        A dict with keys ``oss_key``, ``etag``, ``public_url``,
        and ``presigned_url``.
    """
    bucket = _ensure_bucket()
    path = Path(local_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Local file not found: {local_path}")

    if oss_key is None:
        import time

        oss_key = f"deer-flow/{path.suffix.replace('.', '')}/{int(time.time())}/{path.name}"

    kwargs: dict[str, Any] = {}
    if content_type is not None:
        kwargs["content_type"] = content_type
    if metadata is not None:
        kwargs["headers"] = {
            "x-oss-meta-" + k.lower(): v for k, v in metadata.items()
        }

    result = bucket.put_object_from_file(
        oss_key, str(path), progress_callback=progress_callback, **kwargs
    )
    logger.info(
        "Uploaded %s → oss://%s/%s  (etag=%s, status=%s)",
        path.name, bucket.bucket_name, oss_key, result.etag, result.status,
    )

    public_url = _build_public_url(bucket, oss_key)
    return public_url


def generate_presigned_url(
    oss_key: str,
    *,
    expires_in: int = 3600,
    method: str = "GET",
) -> str:
    """Generate a presigned download URL for an OSS object.

    Args:
        oss_key: Object key in the bucket.
        expires_in: URL validity in seconds (default 1 hour).
        method: HTTP method (default "GET" for download).

    Returns:
        A time-limited presigned URL.
    """
    bucket = _ensure_bucket()
    return bucket.sign_url(method, oss_key, expires_in)


def delete_file(oss_key: str) -> bool:
    """Delete an object from OSS.

    Returns ``True`` if the object existed, ``False`` if it did not.
    """
    bucket = _ensure_bucket()
    exist = bucket.object_exists(oss_key)
    if not exist:
        logger.info("OSS object does not exist: %s", oss_key)
        return False
    bucket.delete_object(oss_key)
    logger.info("Deleted OSS object: %s", oss_key)
    return True


def list_files(
    prefix: str = "",
    *,
    max_keys: int = 100,
) -> list[dict[str, Any]]:
    """List objects in the OSS bucket with the given prefix.

    Returns a list of dicts with ``key``, ``size``, ``last_modified``.
    """
    bucket = _ensure_bucket()
    objects: list[dict[str, Any]] = []
    for obj in bucket.list_objects(prefix=prefix, max_keys=max_keys).object_list:
        objects.append({
            "key": obj.key,
            "size": obj.size,
            "last_modified": obj.last_modified,
        })
    return objects


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_public_url(bucket: Bucket, oss_key: str) -> str:
    """Build the public (non-presigned) HTTPS URL for an OSS object."""
    return f"https://{bucket.bucket_name}.{bucket.endpoint.replace('https://', '').replace('http://', '')}/{oss_key}"
