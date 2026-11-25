from struct import unpack, unpack_from

from ._reader import HeaderReader
from .model import ImageFormat, ImageInfo


def _positive(value: int) -> int | None:
    return value if 0 < value <= 0x7fff_ffff else None


def emf(header: bytes) -> ImageInfo:
    left, top, right, bottom = unpack_from("<4i", header, 8)
    # Both coordinates belong to the device-space rectangle.
    return ImageInfo(ImageFormat.EMF, _positive(right - left + 1),
                     _positive(bottom - top + 1), frame_count=1)


def bmp(header: bytes) -> ImageInfo:
    dib_size = unpack_from("<I", header, 14)[0]
    if dib_size == 12:
        if len(header) < 26:
            return ImageInfo(ImageFormat.BMP)
        width, height, depth = unpack_from("<HH2xH", header, 18)
    else:
        if len(header) < 30:
            return ImageInfo(ImageFormat.BMP)
        width, height, depth = unpack_from("<ii2xH", header, 18)
        height = abs(height)  # A negative height denotes top-down scan lines.
    return ImageInfo(ImageFormat.BMP, _positive(width), _positive(height), _positive(depth), 1)


def psd(header: bytes) -> ImageInfo:
    if len(header) < 24:
        return ImageInfo(ImageFormat.PSD)
    height, width, depth = unpack_from(">IIH", header, 14)
    return ImageInfo(ImageFormat.PSD, _positive(width), _positive(height), _positive(depth), 1)


def wmf(header: bytes) -> ImageInfo:
    if len(header) < 16 or not header.startswith(b"\x9a\xc6\xcd\xd7"):
        return ImageInfo(ImageFormat.WMF)
    left, top, right, bottom, inch = unpack_from("<4hH", header, 6)
    if inch == 0:
        return ImageInfo(ImageFormat.WMF)
    width = abs(right - left) * 96 // inch
    height = abs(bottom - top) * 96 // inch
    return ImageInfo(ImageFormat.WMF, _positive(width), _positive(height), frame_count=1)


def png(reader: HeaderReader) -> ImageInfo:
    reader.skip(8)
    ihdr = reader.read(17)
    if len(ihdr) < 17 or ihdr[:8] != b"\x00\x00\x00\x0dIHDR":
        return ImageInfo(ImageFormat.PNG)
    width, height, depth = unpack_from(">IIB", ihdr, 8)
    frames = _png_frames(reader)
    return ImageInfo(ImageFormat.PNG, _positive(width), _positive(height), _positive(depth), frames)


def _png_frames(reader: HeaderReader) -> int | None:
    reader.skip(8)  # Remaining IHDR fields and CRC.
    while len(chunk := reader.read(8)) == 8:
        size, kind = unpack(">I4s", chunk)
        if kind == b"IDAT":
            return 1
        if kind == b"IEND":
            break
        if kind == b"acTL":
            if size == 8:
                data = reader.read(12)  # Frame count, play count and CRC must all be present.
                if len(data) == 12:
                    return _positive(unpack_from(">I", data)[0])
            break
        if not reader.skip(size + 4):
            break
    return None


def jpeg(reader: HeaderReader) -> ImageInfo:
    reader.skip(2)
    while True:
        byte = reader.read(1)
        if not byte:
            break
        if byte != b"\xff":
            continue
        while (byte := reader.read(1)) == b"\xff":
            pass
        if not byte:
            break
        marker = byte[0]
        if marker == 0x01 or 0xd0 <= marker <= 0xd9:
            continue
        length = reader.read(2)
        if len(length) < 2:
            break
        size = int.from_bytes(length, "big")
        if size < 2 or marker == 0xda:
            break
        if 0xc0 <= marker <= 0xcf and marker not in (0xc4, 0xc8, 0xcc):
            frame = reader.read(5)
            if len(frame) == 5:
                depth, height, width = unpack(">BHH", frame)
                return ImageInfo(ImageFormat.JPEG, _positive(width), _positive(height), _positive(depth), 1)
            break
        if not reader.skip(size - 2):
            break
    return ImageInfo(ImageFormat.JPEG)


def ico(header: bytes) -> ImageInfo:
    if len(header) < 14:
        return ImageInfo(ImageFormat.ICO)
    count, width, height, depth = unpack_from("<HBB4xH", header, 4)
    if count == 0:
        return ImageInfo(ImageFormat.ICO)
    return ImageInfo(ImageFormat.ICO, width or 256, height or 256, depth or None, count)
