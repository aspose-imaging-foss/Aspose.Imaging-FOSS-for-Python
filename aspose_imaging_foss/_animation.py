from struct import unpack, unpack_from

from ._reader import HeaderReader
from .model import ImageFormat, ImageInfo


async def _skip_gif_palette(reader: HeaderReader, flags: int) -> None:
    if flags & 0x80:
        await reader.skip(3 * (2 << (flags & 7)))


async def gif(reader: HeaderReader) -> ImageInfo:
    await reader.skip(6)
    screen = await reader.read(7)
    if len(screen) < 7:
        return ImageInfo(ImageFormat.GIF)
    width, height, flags = unpack_from("<HHB", screen)
    await _skip_gif_palette(reader, flags)

    frames = 0
    while True:
        block = await reader.read(1)
        if block == b"\x21":
            await reader.skip(1)  # Extension label.
        elif block == b"\x2c":
            descriptor = await reader.read(9)
            if len(descriptor) < 9:
                break
            frames += 1
            await _skip_gif_palette(reader, descriptor[8])
            await reader.skip(1)  # LZW minimum code size.
        else:
            break
        # Both image and extension payloads are sequences of size-prefixed sub-blocks.
        while size := await reader.read(1):
            if size == b"\0" or not await reader.skip(size[0]):
                break
    return ImageInfo(ImageFormat.GIF, width or None, height or None, frame_count=frames or None)


async def _webp_frames(reader: HeaderReader, riff_size: int, vp8x_size: int) -> int | None:
    # The reader is just past the ten bytes of VP8X data used for the canvas.
    padded = vp8x_size + (vp8x_size & 1)
    remaining = riff_size - 12 - padded
    if vp8x_size < 10 or remaining < 0 or not await reader.skip(padded - 10):
        return None
    frames = 0
    have_anim = False
    while remaining:
        if remaining < 8:
            return None
        header = await reader.read(8)
        if len(header) < 8:
            return None
        kind, size = unpack("<4sI", header)
        padded = size + (size & 1)
        remaining -= 8 + padded
        if remaining < 0 or not await reader.skip(padded):
            return None
        if kind == b"ANIM":
            if have_anim or size != 6:
                return None
            have_anim = True
        elif kind == b"ANMF":
            if not have_anim or size < 24:
                return None
            frames += 1
    return frames or None


async def webp(reader: HeaderReader) -> ImageInfo:
    header = await reader.read(30)
    kind = header[12:16]
    frames = 1
    if kind == b"VP8 " and len(header) == 30 and header[23:26] == b"\x9d\x01\x2a":
        width, height = unpack_from("<HH", header, 26)
        width &= 0x3fff
        height &= 0x3fff
    elif kind == b"VP8L" and len(header) >= 25 and header[20] == 0x2f:
        packed = unpack_from("<I", header, 21)[0]
        width = 1 + (packed & 0x3fff)
        height = 1 + ((packed >> 14) & 0x3fff)
    elif kind == b"VP8X" and len(header) == 30:
        width = 1 + int.from_bytes(header[24:27], "little")
        height = 1 + int.from_bytes(header[27:30], "little")
        if header[20] & 2:
            riff_size, vp8x_size = unpack_from("<I8xI", header, 4)
            frames = await _webp_frames(reader, riff_size, vp8x_size)
    else:
        return ImageInfo(ImageFormat.WEBP)
    return ImageInfo(ImageFormat.WEBP, width or None, height or None, frame_count=frames)
