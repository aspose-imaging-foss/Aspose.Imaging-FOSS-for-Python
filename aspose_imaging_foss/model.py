from dataclasses import dataclass
from enum import IntEnum


class ImageFormat(IntEnum):
    UNKNOWN = 0
    PNG = 1
    JPEG = 2
    GIF = 3
    BMP = 4
    WEBP = 5
    ICO = 6
    TIFF = 7
    PSD = 8
    EMF = 9
    WMF = 10
    DICOM = 11
    DJVU = 12
    AVIF = 13
    HEIC = 14


@dataclass(frozen=True)
class ImageInfo:
    format: ImageFormat
    width: int | None = None
    height: int | None = None
    bit_depth: int | None = None
    frame_count: int | None = None
