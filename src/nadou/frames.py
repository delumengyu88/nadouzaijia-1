import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from nadou.signed_url import SignedUrlProvider

_DERIVED = frozenset({"lowres", "card", "hd"})


class FrameUnavailable(Exception):
    """No private derived frame exists for this record."""


class DerivedFrameSource(Protocol):
    """Read a derived representative frame. Implementations must not return a URL."""

    def fetch_representative(self, variant: str, record_id: str, user_id: str | None) -> bytes:
        """Return image bytes for one derived variant."""


class FrameByteReader(Protocol):
    """Private bytes for a derived frame. The signed locator never reaches this reader."""

    def read(self, variant: str, record_id: str) -> bytes:
        """Return image bytes, or raise FrameUnavailable."""


class SignedDerivedFrameSource:
    """Ask SignedUrlProvider for a derived variant, then read bytes and drop the locator."""

    def __init__(self, urls: SignedUrlProvider, reader: FrameByteReader) -> None:
        self._urls = urls
        self._reader = reader

    def fetch_representative(self, variant: str, record_id: str, user_id: str | None) -> bytes:
        locator = self._urls.sign_derived(variant, record_id, user_id)
        if not str(locator).startswith("signed://derived/"):
            raise RuntimeError("signer returned a non-derived locator")
        return self._reader.read(variant, record_id)


class GeneratedFrameReader:
    """Synthetic frames for tests and local export. No network and no stored URL."""

    def read(self, variant: str, record_id: str) -> bytes:
        if variant not in _DERIVED:
            raise ValueError("only derived variants can be read")
        return SyntheticFrame.png(record_id)


class MappedFrameReader:
    """Test double: bytes keyed by variant and record id."""

    def __init__(self, images: dict[tuple[str, str], bytes]) -> None:
        self._images = images

    def read(self, variant: str, record_id: str) -> bytes:
        try:
            return self._images[(variant, record_id)]
        except KeyError as exc:
            raise FrameUnavailable(record_id) from exc


class SyntheticFrame:
    """A generated picture. The colors depend on the record id and name no real title."""

    @staticmethod
    def png(record_id: str) -> bytes:
        digest = hashlib.sha256(record_id.encode()).digest()
        color = (digest[0], digest[1], digest[2])
        image = Image.new("RGB", (960, 540), color)
        draw = ImageDraw.Draw(image)
        draw.rectangle((36, 36, 924, 504), outline=(255, 255, 255), width=8)
        inverse = tuple(255 - channel for channel in color)
        draw.rectangle((72, 72, 420, 280), fill=inverse)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


@dataclass(frozen=True)
class PreparedFrame:
    jpeg: bytes
    width: int
    height: int


class WatermarkFont:
    NAME = "DroidSansFallback-subset.ttf"

    @staticmethod
    def path() -> Path:
        bundled = Path(__file__).resolve().parent / "fonts" / WatermarkFont.NAME
        if not bundled.is_file():
            raise FileNotFoundError("watermark font is not packaged")
        return bundled

    @staticmethod
    def load(size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(WatermarkFont.path(), size)


class FrameImage:
    """Scale a derived frame, burn in the watermark, and encode a small JPEG."""

    WIDTH = 600
    TARGET_BYTES = 50 * 1024
    MARK = "那豆在架"

    @staticmethod
    def prepare(raw: bytes) -> PreparedFrame:
        image = Image.open(BytesIO(raw)).convert("RGB")
        image = FrameImage._resize(image)
        FrameImage._watermark(image)
        jpeg = FrameImage._encode(image)
        return PreparedFrame(jpeg=jpeg, width=image.width, height=image.height)

    # Free-tier preview: shapes and colors only, no readable detail.
    BLUR_GRID = 24
    BLUR_RADIUS = 6

    @staticmethod
    def blurred(raw: bytes) -> PreparedFrame:
        """Pixelate and blur before encoding, so the clear frame never leaves the server."""
        image = Image.open(BytesIO(raw)).convert("RGB")
        image = FrameImage._resize(image)
        grid_h = max(1, round(image.height * FrameImage.BLUR_GRID / image.width))
        small = image.resize((FrameImage.BLUR_GRID, grid_h), Image.Resampling.BOX)
        image = small.resize(image.size, Image.Resampling.BILINEAR)
        image = image.filter(ImageFilter.GaussianBlur(FrameImage.BLUR_RADIUS))
        FrameImage._watermark(image)
        jpeg = FrameImage._encode(image)
        return PreparedFrame(jpeg=jpeg, width=image.width, height=image.height)

    @staticmethod
    def _resize(image: Image.Image) -> Image.Image:
        if image.width == FrameImage.WIDTH:
            return image
        height = max(1, round(image.height * FrameImage.WIDTH / image.width))
        return image.resize((FrameImage.WIDTH, height), Image.Resampling.LANCZOS)

    # Small, semi-transparent corner mark: a deterrent that does not hide the picture.
    MARK_HEIGHT = 0.035  # font size as a share of image width
    MARK_MARGIN = 0.015  # gap to the right and bottom edges, as a share of width
    MARK_TEXT_ALPHA = 145  # about 57 %
    MARK_SHADOW_ALPHA = 90
    MARK_BACKING_ALPHA = 56  # light translucent backing, about 22 %

    @staticmethod
    def mark_box(width: int, height: int) -> tuple[int, int, int, int]:
        """Where the mark (with its backing) sits for an image of this size."""
        font = WatermarkFont.load(FrameImage._mark_size(width))
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        left, top, right, bottom = probe.textbbox((0, 0), FrameImage.MARK, font=font)
        pad = max(2, round(width * 0.006))
        margin = max(4, round(width * FrameImage.MARK_MARGIN))
        x1 = width - margin
        y1 = height - margin
        x0 = x1 - (right - left) - 2 * pad
        y0 = y1 - (bottom - top) - 2 * pad
        return x0, y0, x1, y1

    @staticmethod
    def _mark_size(width: int) -> int:
        return max(12, round(width * FrameImage.MARK_HEIGHT))

    @staticmethod
    def _watermark(image: Image.Image) -> None:
        font = WatermarkFont.load(FrameImage._mark_size(image.width))
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        left, top, _right, _bottom = draw.textbbox((0, 0), FrameImage.MARK, font=font)
        x0, y0, x1, y1 = FrameImage.mark_box(image.width, image.height)
        pad = max(2, round(image.width * 0.006))
        draw.rounded_rectangle(
            (x0, y0, x1, y1),
            radius=max(2, pad),
            fill=(0, 0, 0, FrameImage.MARK_BACKING_ALPHA),
        )
        x = x0 + pad - left
        y = y0 + pad - top
        draw.text(
            (x + 1, y + 1), FrameImage.MARK, font=font, fill=(0, 0, 0, FrameImage.MARK_SHADOW_ALPHA)
        )
        draw.text(
            (x, y), FrameImage.MARK, font=font, fill=(255, 255, 255, FrameImage.MARK_TEXT_ALPHA)
        )
        merged = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        image.paste(merged)

    @staticmethod
    def _encode(image: Image.Image) -> bytes:
        quality = 70
        best = b""
        while quality >= 30:
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
            best = buffer.getvalue()
            if len(best) <= FrameImage.TARGET_BYTES:
                return best
            quality -= 8
        return best
