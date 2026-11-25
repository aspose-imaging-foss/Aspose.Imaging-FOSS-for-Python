from struct import unpack

from ._reader import HeaderReader
from .model import ImageFormat, ImageInfo


_IMPLICIT_LE = b"1.2.840.10008.1.2"
_EXPLICIT_BE = b"1.2.840.10008.1.2.2"
_LONG_VRS = {b"OB", b"OW", b"OF", b"OD", b"OL", b"SQ", b"UC", b"UR", b"UT", b"UN"}
_ROWS, _COLUMNS, _BITS_ALLOCATED = 0x0010, 0x0011, 0x0100


def _length(reader: HeaderReader, implicit: bool, byteorder: str) -> int | None:
    data = reader.read(4)
    if len(data) != 4:
        return None
    if not implicit:
        if data[:2] not in _LONG_VRS:
            return int.from_bytes(data[2:], byteorder)
        data = reader.read(4)  # VR and reserved bytes precede the 32-bit length.
        if len(data) != 4:
            return None
    length = int.from_bytes(data, byteorder)
    return None if length == 0xffff_ffff else length


def dicom(reader: HeaderReader) -> ImageInfo:
    reader.skip(132)
    syntax = b""
    tag = reader.read(4)
    # Group 0002 always uses Explicit VR Little Endian. Retain the first dataset tag.
    while len(tag) == 4 and tag[:2] == b"\x02\x00":
        length = _length(reader, False, "little")
        if length is None:
            return ImageInfo(ImageFormat.DICOM)
        if tag[2:] == b"\x10\x00":
            if length > 64:
                return ImageInfo(ImageFormat.DICOM)
            value = reader.read(length)
            if len(value) != length:
                return ImageInfo(ImageFormat.DICOM)
            syntax = value.rstrip(b"\x00 ")
        elif not reader.skip(length):
            return ImageInfo(ImageFormat.DICOM)
        tag = reader.read(4)

    byteorder = "big" if syntax == _EXPLICIT_BE else "little"
    tag_layout = ">HH" if byteorder == "big" else "<HH"
    fields = {}
    for index in range(100_000):
        if index:
            tag = reader.read(4)
        if len(tag) != 4:
            break
        group, element = unpack(tag_layout, tag)
        if (group, element) == (0x7fe0, 0x0010):
            break
        length = _length(reader, syntax == _IMPLICIT_LE, byteorder)
        if length is None:
            break
        if group == 0x0028 and element in (_ROWS, _COLUMNS, _BITS_ALLOCATED) and length == 2:
            value = reader.read(2)
            if len(value) != 2:
                break
            fields[element] = int.from_bytes(value, byteorder) or None
            if len(fields) == 3:
                break
        elif not reader.skip(length):
            break
    return ImageInfo(ImageFormat.DICOM, fields.get(_COLUMNS), fields.get(_ROWS),
                     fields.get(_BITS_ALLOCATED))
