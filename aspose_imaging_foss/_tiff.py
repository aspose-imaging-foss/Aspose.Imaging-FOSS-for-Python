from struct import unpack_from

from ._metadata import _positive
from ._reader import OffsetReader
from .model import ImageFormat, ImageInfo


async def _entry_value(reader: OffsetReader, entry: bytes, order: str) -> int | None:
    value_type, count = unpack_from(order + "HI", entry, 2)
    size = {3: 2, 4: 4}.get(value_type)  # TIFF SHORT and LONG
    if size is None or count == 0:
        return None
    if size * count <= 4:
        data = entry[8:8 + size]
    else:
        offset = unpack_from(order + "I", entry, 8)[0]
        data = await reader.read_at(offset, size)
        if len(data) < size:
            return None
    return _positive(int.from_bytes(data, "little" if order == "<" else "big"))


async def tiff(reader: OffsetReader) -> ImageInfo:
    header = (await reader.read_at(0, 8)).ljust(8, b"\0")
    order = "<" if header[:2] == b"II" else ">"
    offset = unpack_from(order + "I", header, 4)[0]
    visited: set[int] = set()
    fields: dict[int, int | None] = {}
    while offset and offset not in visited and len(visited) < 65536:
        data = await reader.read_at(offset, 2)
        if len(data) < 2:
            break
        count = unpack_from(order + "H", data)[0]
        visited.add(offset)
        entries = offset + 2
        if len(visited) == 1:
            for index in range(count):
                entry = await reader.read_at(entries + index * 12, 12)
                if len(entry) < 12:
                    break
                tag = unpack_from(order + "H", entry)[0]
                if tag in (256, 257, 258):
                    fields[tag] = await _entry_value(reader, entry, order)
        data = await reader.read_at(entries + count * 12, 4)
        if len(data) < 4:
            break
        offset = unpack_from(order + "I", data)[0]
    return ImageInfo(ImageFormat.TIFF, fields.get(256), fields.get(257),
                     fields.get(258), len(visited) or None)
