from collections.abc import Iterator
from struct import unpack, unpack_from

from ._reader import OffsetReader
from .model import ImageFormat, ImageInfo


def _chunks(reader: OffsetReader, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    while start + 8 <= end:
        header = reader.read_at(start, 8)
        if len(header) < 8:
            break
        kind, size = unpack(">4sI", header)
        data = start + 8
        yield kind, data, data + size
        if size == 0:
            break
        start = data + size + (size & 1)


def _page_size(reader: OffsetReader, start: int, end: int) -> tuple[int | None, int | None]:
    for kind, data, stop in _chunks(reader, start, end):
        if kind == b"INFO" and stop - data >= 4:
            dimensions = reader.read_at(data, 4)
            if len(dimensions) == 4:
                width, height = unpack(">HH", dimensions)
                return width or None, height or None
            break
    return None, None


def djvu(reader: OffsetReader) -> ImageInfo:
    header = reader.read_at(0, 16)
    if len(header) < 16:
        return ImageInfo(ImageFormat.DJVU)
    size, kind = unpack_from(">I4s", header, 8)
    end = 12 + size
    if kind == b"DJVU":
        width, height = _page_size(reader, 16, end)
        return ImageInfo(ImageFormat.DJVU, width, height, frame_count=1)
    if kind != b"DJVM":
        return ImageInfo(ImageFormat.DJVU)

    bundled = False
    pages = 0
    width = height = None
    for kind, start, stop in _chunks(reader, 16, end):
        if kind == b"DIRM":
            flags = reader.read_at(start, 1) if stop > start else b""
            bundled = bool(flags and flags[0] & 0x80)
        elif kind == b"FORM" and reader.read_at(start, 4) == b"DJVU":
            pages += 1
            if width is None:
                width, height = _page_size(reader, start + 4, stop)
    frames = pages if bundled and end <= reader.length and pages else None
    return ImageInfo(ImageFormat.DJVU, width, height, frame_count=frames)
