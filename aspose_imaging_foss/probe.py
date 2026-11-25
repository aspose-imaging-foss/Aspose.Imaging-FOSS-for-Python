from os import PathLike
from struct import unpack_from
from typing import BinaryIO

from . import _metadata
from ._reader import HeaderReader, read_up_to
from .model import ImageFormat, ImageInfo


Source = bytes | bytearray | memoryview | str | PathLike[str] | BinaryIO
_HEADER_SIZE = 132  # Includes the DICOM marker after its 128-byte preamble.
_AVIF_BRANDS = {b"avif", b"avis"}
_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs"}


def _read_prefix(stream: BinaryIO) -> bytes:
    if stream.seekable():
        stream.seek(0)
    return read_up_to(stream, _HEADER_SIZE)


def _byte_view(source: bytes | bytearray | memoryview) -> memoryview:
    view = memoryview(source)
    if view.c_contiguous:
        return view.cast("B")
    return memoryview(view.tobytes())


def _header(source: Source) -> bytes:
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(_byte_view(source)[:_HEADER_SIZE])
    if isinstance(source, (str, PathLike)):
        with open(source, "rb") as stream:
            return _read_prefix(stream)
    return _read_prefix(source)


def _file_type(header: bytes) -> ImageFormat:
    major = header[8:12]
    if major in _HEIC_BRANDS:
        return ImageFormat.HEIC
    if major in _AVIF_BRANDS:
        return ImageFormat.AVIF
    size = int.from_bytes(header[:4], "big")
    end = min(size or len(header), len(header))
    for offset in range(16, end - 3, 4):
        if header[offset:offset + 4] in _AVIF_BRANDS:
            return ImageFormat.AVIF
    if major in (b"mif1", b"mif2"):
        return ImageFormat.HEIC
    return ImageFormat.UNKNOWN


def _identify(header: bytes) -> ImageFormat:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ImageFormat.PNG
    if header.startswith(b"\xff\xd8\xff"):
        return ImageFormat.JPEG
    if header.startswith((b"GIF87a", b"GIF89a")):
        return ImageFormat.GIF
    if header.startswith(b"BM") and len(header) >= 18:
        dib_size = unpack_from("<I", header, 14)[0]
        if dib_size in (12, 108, 124) or 16 <= dib_size <= 64:
            return ImageFormat.BMP
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return ImageFormat.WEBP
    if header.startswith(b"\x00\x00\x01\x00"):
        return ImageFormat.ICO
    if header.startswith((b"II*\x00", b"MM\x00*")):
        return ImageFormat.TIFF
    if header.startswith(b"8BPS"):
        return ImageFormat.PSD
    if header.startswith(b"\x01\x00\x00\x00") and header[40:44] == b" EMF":
        return ImageFormat.EMF
    if len(header) >= 6:
        if header.startswith(b"\x9a\xc6\xcd\xd7"):
            return ImageFormat.WMF
        kind, words, version = unpack_from("<3H", header)
        if kind in (1, 2) and words == 9 and version in (0x0100, 0x0300):
            return ImageFormat.WMF
    if header[128:132] == b"DICM":
        return ImageFormat.DICOM
    if header.startswith(b"AT&TFORM"):
        return ImageFormat.DJVU
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return _file_type(header)
    return ImageFormat.UNKNOWN


def detect_format(source: Source) -> ImageFormat:
    """Identify the format from at most 132 bytes of a file, buffer or stream."""
    return _identify(_header(source))


_FIXED_HEADERS = {
    ImageFormat.BMP: _metadata.bmp,
    ImageFormat.PSD: _metadata.psd,
    ImageFormat.EMF: _metadata.emf,
    ImageFormat.WMF: _metadata.wmf,
}


def _probe(buffered: memoryview, stream: BinaryIO | None = None) -> ImageInfo:
    header = bytes(buffered[:_HEADER_SIZE])
    image_format = _identify(header)
    if image_format == ImageFormat.PNG:
        return _metadata.png(HeaderReader(buffered, stream))
    if image_format == ImageFormat.JPEG:
        return _metadata.jpeg(HeaderReader(buffered, stream))
    handler = _FIXED_HEADERS.get(image_format)
    if handler is not None:
        return handler(header)
    return ImageInfo(image_format)


def probe(source: Source) -> ImageInfo:
    """Identify the image format and read its supported header metadata."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        return _probe(_byte_view(source))
    if isinstance(source, (str, PathLike)):
        with open(source, "rb") as stream:
            return _probe(memoryview(_read_prefix(stream)), stream)
    return _probe(memoryview(_read_prefix(source)), source)
