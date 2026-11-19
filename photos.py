"""Crop photo uploads: check and clean each image, then store it in Azure.

* The image file goes to Azure Blob Storage (a private container).
* A record of the upload (state, district, crop, note, time, sizes) goes to
  Azure Table Storage in the same storage account.
* The page shows photos through short-lived read-only SAS links, so the
  container never has to be public.
"""

import io
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 40_000_000           # refuse "decompression bomb" images
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
FULL_SIZE = 1600                  # longest side of the stored photo, in pixels
THUMB_SIZE = 480                  # longest side of the gallery thumbnail
LINK_LIFETIME = timedelta(minutes=30)
MAX_NOTE_LENGTH = 280
PARTITION = "photo"
MAX_TIMESTAMP_MS = 10 ** 13       # RowKey = MAX - now, so newest rows sort first

Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class PhotoError(ValueError):
    """A problem with the uploaded file that the user can fix."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def clean_text(value, limit):
    """Collapse whitespace, drop control characters and cut to `limit` characters."""
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value or "")
    return re.sub(r"\s+", " ", value).strip()[:limit]


def process_image(raw):
    """Validate an uploaded image and return a cleaned JPEG plus a thumbnail.

    Re-encoding drops every bit of metadata (EXIF, GPS location, camera serial),
    and also neutralises anything hidden in the original file.
    """
    if not raw:
        raise PhotoError("no_file", "No photo was uploaded.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise PhotoError("too_large", "The photo is larger than 5 MB.")

    try:
        with Image.open(io.BytesIO(raw)) as probe:
            original_format = probe.format
            if original_format not in ALLOWED_FORMATS:
                raise PhotoError("wrong_type", "Only JPG, PNG or WEBP photos are allowed.")
            if probe.width * probe.height > MAX_PIXELS:
                raise PhotoError("too_many_pixels", "The photo has too many pixels.")
            probe.verify()

        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            image = ImageOps.exif_transpose(image)  # keep phone photos the right way up
            image = _to_rgb(image)
    except PhotoError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise PhotoError("wrong_type", "The file is not a valid JPG, PNG or WEBP image.") from None

    full = image.copy()
    full.thumbnail((FULL_SIZE, FULL_SIZE), Image.Resampling.LANCZOS)
    thumb = image.copy()
    thumb.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)

    return {
        "full": _jpeg(full, 85),
        "thumb": _jpeg(thumb, 78),
        "width": full.width,
        "height": full.height,
        "original_format": original_format,
        "original_bytes": len(raw),
    }


def _to_rgb(image):
    if image.mode in ("RGBA", "LA", "P", "PA"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.getchannel("A"))
        return background
    return image.convert("RGB")


def _jpeg(image, quality):
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality, optimize=True, progressive=True)
    return buffer.getvalue()


class PhotoStore:
    def __init__(self, connection_string, container="crop-photos", table="CropPhotos"):
        # Imported here so the rest of the app runs without the Azure SDK configured.
        from azure.data.tables import TableServiceClient
        from azure.storage.blob import BlobServiceClient

        self.blob_service = BlobServiceClient.from_connection_string(connection_string)
        account_key = getattr(self.blob_service.credential, "account_key", None)
        if not account_key:
            raise ValueError(
                "AZURE_STORAGE_CONNECTION_STRING must contain an AccountKey "
                "(copy it from the storage account's Access keys page)."
            )
        self._account_key = account_key
        self.container = self.blob_service.get_container_client(container)
        self.table = TableServiceClient.from_connection_string(connection_string).get_table_client(table)
        self._ready = False
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls):
        connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
        if not connection_string:
            return None
        return cls(
            connection_string,
            container=os.environ.get("AZURE_STORAGE_CONTAINER", "crop-photos"),
            table=os.environ.get("AZURE_STORAGE_TABLE", "CropPhotos"),
        )

    def ensure_ready(self):
        """Create the private container and the table on first use."""
        if self._ready:
            return
        from azure.core.exceptions import ResourceExistsError

        with self._lock:
            if self._ready:
                return
            try:
                self.container.create_container()  # no public access: private by default
            except ResourceExistsError:
                pass
            try:
                self.table.create_table()
            except ResourceExistsError:
                pass
            self._ready = True

    def save(self, processed, details):
        from azure.storage.blob import ContentSettings

        self.ensure_ready()
        now = datetime.now(timezone.utc)
        photo_id = uuid.uuid4().hex
        folder = now.strftime("%Y/%m/%d")
        blob_name = f"photos/{folder}/{photo_id}.jpg"
        thumb_name = f"thumbs/{folder}/{photo_id}.jpg"
        settings = ContentSettings(content_type="image/jpeg", cache_control="private, max-age=1800")

        self.container.upload_blob(blob_name, processed["full"], overwrite=False, content_settings=settings)
        self.container.upload_blob(thumb_name, processed["thumb"], overwrite=False, content_settings=settings)

        entity = {
            "PartitionKey": PARTITION,
            "RowKey": f"{MAX_TIMESTAMP_MS - int(now.timestamp() * 1000):013d}_{photo_id}",
            "photo_id": photo_id,
            "blob_name": blob_name,
            "thumb_name": thumb_name,
            "state": details["state"],
            "district": details["district"],
            "crop": details.get("crop", ""),
            "note": details.get("note", ""),
            "language": details.get("language", "en"),
            "width": processed["width"],
            "height": processed["height"],
            "size_bytes": len(processed["full"]),
            "original_format": processed["original_format"],
            "original_bytes": processed["original_bytes"],
            "uploaded_at": now.isoformat(timespec="seconds"),
        }
        try:
            self.table.create_entity(entity)
        except Exception:
            # Don't leave orphaned files behind if the record could not be written.
            for name in (blob_name, thumb_name):
                try:
                    self.container.delete_blob(name)
                except Exception:
                    pass
            raise
        return self._public(entity)

    def list_recent(self, limit=12, state=None, district=None):
        self.ensure_ready()
        query = "PartitionKey eq @pk"
        parameters = {"pk": PARTITION}
        if state:
            query += " and state eq @state"
            parameters["state"] = state
        if district:
            query += " and district eq @district"
            parameters["district"] = district

        records = []
        # Rows come back in RowKey order, which is newest first.
        for entity in self.table.query_entities(query, parameters=parameters, results_per_page=limit):
            records.append(self._public(entity))
            if len(records) >= limit:
                break
        return records

    def _public(self, entity):
        return {
            "id": entity["photo_id"],
            "state": entity["state"],
            "district": entity["district"],
            "crop": entity.get("crop", ""),
            "note": entity.get("note", ""),
            "uploaded_at": entity["uploaded_at"],
            "width": entity.get("width"),
            "height": entity.get("height"),
            "url": self._read_link(entity["blob_name"]),
            "thumb_url": self._read_link(entity["thumb_name"]),
        }

    def _read_link(self, blob_name):
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        now = datetime.now(timezone.utc)
        token = generate_blob_sas(
            account_name=self.blob_service.account_name,
            container_name=self.container.container_name,
            blob_name=blob_name,
            account_key=self._account_key,
            permission=BlobSasPermissions(read=True),
            start=now - timedelta(minutes=5),
            expiry=now + LINK_LIFETIME,
        )
        return f"{self.container.get_blob_client(blob_name).url}?{token}"
