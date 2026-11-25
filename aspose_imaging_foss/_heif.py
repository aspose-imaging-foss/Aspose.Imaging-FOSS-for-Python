from collections.abc import Iterator
from struct import unpack, unpack_from
from typing import NamedTuple

from ._metadata import _positive
from ._reader import OffsetReader
from .model import ImageFormat, ImageInfo


class _Box(NamedTuple):
    kind: bytes
    start: int  # First payload byte.
    end: int


def _boxes(reader: OffsetReader, start: int, end: int) -> Iterator[_Box]:
    while start + 8 <= end:
        header = reader.read_at(start, 8)
        if len(header) < 8:
            return
        size, kind = unpack(">I4s", header)
        header_size = 8
        if size == 1:
            extended = reader.read_at(start + 8, 8)
            if len(extended) < 8:
                return
            size = int.from_bytes(extended, "big")
            header_size = 16
        elif size == 0:
            size = end - start
        if size < header_size:
            return
        stop = min(start + size, end)
        yield _Box(kind, start + header_size, stop)
        start = stop


def _find(reader: OffsetReader, start: int, end: int, kind: bytes) -> _Box | None:
    return next((box for box in _boxes(reader, start, end) if box.kind == kind), None)


def _payload(reader: OffsetReader, box: _Box, limit: int) -> bytes:
    size = box.end - box.start
    return reader.read_at(box.start, size) if 0 < size <= limit else b""


def _primary_id(reader: OffsetReader, pitm: _Box) -> int | None:
    data = _payload(reader, pitm, 64)
    if len(data) < 6:
        return None
    size = 2 if data[0] == 0 else 4
    if len(data) < 4 + size:
        return None
    return int.from_bytes(data[4:4 + size], "big")


def _associated_size(data: bytes, primary: int, sizes: dict[int, tuple[int, int]]) -> tuple[int, int] | None:
    if len(data) < 8:
        return None
    id_bytes = 2 if data[0] == 0 else 4
    wide = data[3] & 1
    index_bytes = 2 if wide else 1
    index_mask = 0x7fff if wide else 0x7f  # Discard the essential-property flag.
    entries = unpack_from(">I", data, 4)[0]
    pos = 8
    for _ in range(entries):
        id_end = pos + id_bytes
        if id_end >= len(data):
            break
        item = int.from_bytes(data[pos:id_end], "big")
        pos = id_end + 1
        end = pos + data[id_end] * index_bytes
        if end > len(data):
            break
        if item == primary:
            for offset in range(pos, end, index_bytes):
                index = int.from_bytes(data[offset:offset + index_bytes], "big") & index_mask
                if index in sizes:
                    return sizes[index]
            break
        pos = end
    return None


def heif(reader: OffsetReader, image_format: ImageFormat) -> ImageInfo:
    meta = _find(reader, 0, reader.length, b"meta")
    if meta is None:
        return ImageInfo(image_format)
    children = meta.start + 4  # Skip the FullBox version and flags.
    iprp = _find(reader, children, meta.end, b"iprp")
    if iprp is None:
        return ImageInfo(image_format)
    ipco = _find(reader, iprp.start, iprp.end, b"ipco")
    if ipco is None:
        return ImageInfo(image_format)

    sizes = {}
    for index, prop in enumerate(_boxes(reader, ipco.start, ipco.end), 1):
        if index > 0x7fff:
            break
        if prop.kind == b"ispe":
            data = _payload(reader, prop, 64)
            if len(data) >= 12:
                sizes[index] = unpack_from(">II", data, 4)
    if not sizes:
        return ImageInfo(image_format)

    chosen = None
    pitm = _find(reader, children, meta.end, b"pitm")
    primary = _primary_id(reader, pitm) if pitm is not None else None
    if primary is not None:
        for box in _boxes(reader, iprp.start, iprp.end):
            if box.kind == b"ipma":
                chosen = _associated_size(_payload(reader, box, 1024 * 1024), primary, sizes)
                if chosen is not None:
                    break
    if chosen is None:
        # The C# reader takes the last property when multiple areas tie.
        chosen = max(reversed(sizes.values()), key=lambda size: size[0] * size[1])
    return ImageInfo(image_format, _positive(chosen[0]), _positive(chosen[1]))
