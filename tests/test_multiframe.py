import io
import unittest
from pathlib import Path
from struct import pack

from aspose_imaging_foss import ImageFormat, ImageInfo, probe
from test_probe import ShortStream


WEBP_ANIMATION = Path(__file__).parent / "data" / "animation.webp"


def gif_screen():
    # Two-entry global palette, followed by a comment spanning the detection buffer.
    return b"GIF89a" + pack("<HHBBB", 40, 20, 0x80, 0, 0) + bytes(6) + b"!\xfe\xff" + bytes(255) + b"\0"


def gif_frame():
    # Include block introducers inside the local palette and compressed data.
    return b"," + pack("<4HB", 0, 0, 3, 3, 0x80) + b";,!,;!" + b"\x02\x03,!;\0"


def riff_chunk(kind, data):
    return pack("<4sI", kind, len(data)) + data + bytes(len(data) & 1)


def webp(*chunks):
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + pack("<I", len(body)) + body


def tiff(order):
    marker = b"II" if order == "<" else b"MM"
    # Two directories in reverse physical order. BitsPerSample lives past the prefix.
    header = marker + pack(order + "HI", 42, 32)
    second = pack(order + "HI", 0, 0)
    width = pack(order + "HHII", 256, 4, 1, 1024)
    height = pack(order + "HHIHH", 257, 3, 1, 512, 0)
    depth = pack(order + "HHII", 258, 3, 3, 170)
    first = pack(order + "H", 3) + width + height + depth + pack(order + "I", 8)
    return (header + second).ljust(32, b"\0") + first + bytes(96) + pack(order + "3H", 12, 12, 12)


def iff(kind, data):
    return pack(">4sI", kind, len(data)) + data + bytes(len(data) & 1)


def page(width, height):
    return iff(b"FORM", b"DJVU" + iff(b"INFO", pack(">HH", width, height)))


def document(*components):
    return b"AT&T" + iff(b"FORM", b"DJVM" + b"".join(components))


class MultiframeTests(unittest.TestCase):
    def assert_sources(self, data, expected):
        stream = io.BytesIO(data)
        stream.seek(len(data))
        pipe = ShortStream(b"prefix" + data)
        io.BytesIO.seek(pipe, 6)
        for source in (data, memoryview(data), stream, pipe):
            with self.subTest(source=type(source)):
                self.assertEqual(probe(source), expected)
        self.assertFalse(stream.closed)
        self.assertFalse(pipe.closed)

    def test_gif_palettes_extensions_and_truncation(self):
        screen, frame = gif_screen(), gif_frame()
        for tail, count in [(b"", None), (frame[:9], None), (frame[:10], 1),
                            (frame * 2 + b";" + frame, 2), (frame + b"?" + frame, 1)]:
            with self.subTest(tail=tail):
                self.assert_sources(screen + tail, ImageInfo(ImageFormat.GIF, 40, 20, frame_count=count))

    def test_webp_static_variants(self):
        lossy = riff_chunk(b"VP8 ", bytes(3) + b"\x9d\x01\x2a" + pack("<HH", 0xc020, 0xc010))
        lossless = riff_chunk(b"VP8L", b"/" + pack("<I", 31 | (15 << 14)))
        extended = riff_chunk(b"VP8X", bytes(4) + b"\x1f\0\0\x0f\0\0")
        for chunk in (lossy, lossless, extended):
            self.assert_sources(webp(chunk), ImageInfo(ImageFormat.WEBP, 32, 16, frame_count=1))
        self.assertEqual(probe(webp(lossy).replace(b"\x9d", b"\0")), ImageInfo(ImageFormat.WEBP))

    def test_webp_animation_structure_and_riff_boundary(self):
        self.assertEqual(probe(WEBP_ANIMATION), ImageInfo(ImageFormat.WEBP, 12, 7, frame_count=3))
        self.assert_sources(WEBP_ANIMATION.read_bytes()[:-1], ImageInfo(ImageFormat.WEBP, 12, 7))
        canvas = riff_chunk(b"VP8X", b"\x02" + bytes(9))
        anim = riff_chunk(b"ANIM", bytes(6))
        frame = riff_chunk(b"ANMF", bytes(24))
        padding = riff_chunk(b"XMP ", b"ANMF!")
        for chunks, count in [([anim, frame, padding, frame], 2), ([frame], None),
                              ([anim, frame, anim], None), ([anim, riff_chunk(b"ANMF", bytes(23))], None)]:
            data = webp(canvas, *chunks) + frame  # Trailing bytes are outside RIFF.
            self.assert_sources(data, ImageInfo(ImageFormat.WEBP, 1, 1, frame_count=count))

    def test_ico_first_directory_entry(self):
        data = pack("<HHHBBBBHH", 0, 1, 4, 0, 16, 0, 0, 1, 32)
        self.assert_sources(data, ImageInfo(ImageFormat.ICO, 256, 16, 32, 4))
        self.assertEqual(probe(data[:-1]), ImageInfo(ImageFormat.ICO))
        self.assertEqual(probe(data[:-2] + bytes(2)), ImageInfo(ImageFormat.ICO, 256, 16, None, 4))
        self.assertEqual(probe(data[:4] + bytes(2) + data[6:]), ImageInfo(ImageFormat.ICO))

    def test_tiff_page_chain_and_depth_per_channel(self):
        for order in ("<", ">"):
            data = tiff(order)
            self.assert_sources(data, ImageInfo(ImageFormat.TIFF, 1024, 512, 12, 2))
            self.assert_sources(data[:170], ImageInfo(ImageFormat.TIFF, 1024, 512, None, 2))
            cycle = data[:10] + pack(order + "I", 32) + data[14:]
            self.assert_sources(cycle, ImageInfo(ImageFormat.TIFF, 1024, 512, 12, 2))
            self.assert_sources(data[:46], ImageInfo(ImageFormat.TIFF, 1024, frame_count=1))

    def test_djvu_page_counts_and_document_boundaries(self):
        directory = iff(b"DIRM", b"\x80\0\x03")
        dictionary = iff(b"FORM", b"DJVI" + bytes(141))
        first = page(72, 48)
        self.assert_sources(b"AT&T" + first, ImageInfo(ImageFormat.DJVU, 72, 48, frame_count=1))
        full = document(directory, dictionary, first, page(90, 60))
        self.assert_sources(full + page(1, 1), ImageInfo(ImageFormat.DJVU, 72, 48, frame_count=2))
        self.assert_sources(full[:-1], ImageInfo(ImageFormat.DJVU, 72, 48))
        self.assert_sources(document(iff(b"DIRM", b"\x01")), ImageInfo(ImageFormat.DJVU))
