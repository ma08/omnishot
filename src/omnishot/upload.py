"""S3 upload and presigned URL generation."""

import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import quote

import boto3
from botocore.exceptions import ClientError

from .notify import log

# Ensure common image types resolve correctly (mimetypes can be inconsistent across OS)
_MIME_OVERRIDES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".bmp": "image/bmp",
    ".heic": "image/heic",
}


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _MIME_OVERRIDES:
        return _MIME_OVERRIDES[suffix]
    guess, _ = mimetypes.guess_type(path.name)
    return guess or "application/octet-stream"


def upload_to_s3(
    local_path: Path,
    s3_key: str,
    bucket: str,
    verbose: bool = False,
    diagnostics: dict[str, Any] | None = None,
) -> bool:
    """Upload a file to S3. Returns True on success."""
    s3 = boto3.client("s3")
    content_type = _content_type(local_path)
    if diagnostics is not None:
        diagnostics.update({
            "bucket": bucket,
            "s3_key": s3_key,
            "local_path": str(local_path),
            "content_type": content_type,
        })

    log(f"[upload] Uploading {local_path.name} → s3://{bucket}/{s3_key}", verbose, is_debug=True)

    try:
        s3.upload_file(
            str(local_path),
            bucket,
            s3_key,
            ExtraArgs={
                "ContentType": content_type,
                "ContentDisposition": "inline",
            },
        )
        if diagnostics is not None:
            diagnostics["uploaded"] = True
        return True
    except ClientError as e:
        if diagnostics is not None:
            diagnostics["uploaded"] = False
            diagnostics["error"] = str(e)
            diagnostics["error_type"] = e.__class__.__name__
        log(f"[upload] S3 upload failed: {e}", verbose=True)
        return False
    except Exception as e:
        if diagnostics is not None:
            diagnostics["uploaded"] = False
            diagnostics["error"] = str(e)
            diagnostics["error_type"] = e.__class__.__name__
        log(f"[upload] Unexpected upload error: {e}", verbose=True)
        return False


def generate_presigned_url(
    bucket: str,
    key: str,
    expiry: int = 86400,
    verbose: bool = False,
    diagnostics: dict[str, Any] | None = None,
) -> str | None:
    """Generate a presigned GET URL. Returns URL string or None on failure."""
    s3 = boto3.client("s3")

    # Derive content type from the key's extension so response headers are explicit.
    # This ensures tools fetching the URL see correct Content-Type and inline
    # disposition even if they ignore the S3 object metadata.
    suffix = Path(key).suffix.lower()
    content_type = _MIME_OVERRIDES.get(suffix)
    if not content_type:
        guess, _ = mimetypes.guess_type(key)
        content_type = guess or "application/octet-stream"
    if diagnostics is not None:
        diagnostics.update({
            "bucket": bucket,
            "s3_key": key,
            "expiry": expiry,
            "content_type": content_type,
        })

    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ResponseContentType": content_type,
                "ResponseContentDisposition": "inline",
            },
            ExpiresIn=expiry,
        )
        log(f"[upload] Presigned URL generated (expires in {expiry}s)", verbose, is_debug=True)
        if diagnostics is not None:
            diagnostics["url_generated"] = True
        return url
    except ClientError as e:
        if diagnostics is not None:
            diagnostics["url_generated"] = False
            diagnostics["error"] = str(e)
            diagnostics["error_type"] = e.__class__.__name__
        log(f"[upload] Presigned URL generation failed: {e}", verbose=True)
        return None
    except Exception as e:
        if diagnostics is not None:
            diagnostics["url_generated"] = False
            diagnostics["error"] = str(e)
            diagnostics["error_type"] = e.__class__.__name__
        log(f"[upload] Presigned URL generation failed: {e}", verbose=True)
        return None


def make_object_public(
    bucket: str,
    key: str,
    verbose: bool = False,
    diagnostics: dict[str, Any] | None = None,
) -> bool:
    """Set object ACL to public-read."""
    s3 = boto3.client("s3")
    if diagnostics is not None:
        diagnostics.update({
            "bucket": bucket,
            "s3_key": key,
            "acl": "public-read",
        })

    try:
        s3.put_object_acl(Bucket=bucket, Key=key, ACL="public-read")
        if diagnostics is not None:
            diagnostics["updated"] = True
        return True
    except ClientError as e:
        if diagnostics is not None:
            diagnostics["updated"] = False
            diagnostics["error"] = str(e)
            diagnostics["error_type"] = e.__class__.__name__
            diagnostics["error_code"] = (
                e.response.get("Error", {}).get("Code") if hasattr(e, "response") else None
            )
            diagnostics["error_message"] = (
                e.response.get("Error", {}).get("Message") if hasattr(e, "response") else None
            )
        log(f"[upload] Failed to update object ACL: {e}", verbose=True)
        return False
    except Exception as e:
        if diagnostics is not None:
            diagnostics["updated"] = False
            diagnostics["error"] = str(e)
            diagnostics["error_type"] = e.__class__.__name__
        log(f"[upload] Failed to update object ACL: {e}", verbose=True)
        return False


def build_public_url(bucket: str, key: str, public_base_url: str | None = None) -> str:
    """Build a stable public URL for an S3 object key."""
    encoded_key = quote(key, safe="/")
    if public_base_url:
        return f"{public_base_url.rstrip('/')}/{encoded_key}"
    return f"https://{bucket}.s3.amazonaws.com/{encoded_key}"
