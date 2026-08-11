import hashlib
import io

from PIL import Image, ImageOps, UnidentifiedImageError

from campaign_worker.errors import WorkflowOperationError

from .models import NormalizedImage


class ImageProcessor:
    def __init__(self, max_download_bytes: int = 25_000_000) -> None:
        self.max_download_bytes = max_download_bytes

    def normalize(self, source: bytes) -> NormalizedImage:
        if len(source) > self.max_download_bytes:
            raise WorkflowOperationError("ARTIFACT_VALIDATION_FAILED", "image exceeds size limit", retryable=False)
        source_checksum = hashlib.sha256(source).hexdigest()
        try:
            with Image.open(io.BytesIO(source)) as decoded:
                decoded.load()
                normalized = ImageOps.exif_transpose(decoded).convert("RGB")
                fitted = ImageOps.fit(normalized, (1080, 1920), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
                output = io.BytesIO()
                fitted.save(output, format="JPEG", quality=90, optimize=False, progressive=False, subsampling=2)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise WorkflowOperationError(
                "ARTIFACT_VALIDATION_FAILED", "downloaded content is not a valid image", retryable=False
            ) from exc
        data = output.getvalue()
        return NormalizedImage(
            data=data,
            width=1080,
            height=1920,
            checksum_sha256=hashlib.sha256(data).hexdigest(),
            source_checksum_sha256=source_checksum,
        )
