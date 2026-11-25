import io
from pathlib import Path
from struct import pack
import unittest

from aspose_imaging_foss import ImageFormat, ImageInfo, probe
from test_probe import ShortStream


def chunk(kind, data):
    return pack(">I4s", len(data), kind) + data + bytes(4)


def png(width=23, height=11, depth=8):
    ihdr = pack(">IIB", width, height, depth) + bytes(4)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)


def bmp(size, width, height, depth):
    layout = "<HHHH" if size == 12 else "<iiHH"
    return b"BM" + bytes(12) + pack("<I", size) + pack(layout, width, height, 1, depth)


def wmf(left, top, right, bottom, inch):
    return bytes.fromhex("9a c6 cd d7 00 00") + pack("<4hH", left, top, right, bottom, inch)


def jpeg_segment(marker, data):
    return bytes([0xff, marker]) + pack(">H", len(data) + 2) + data


class MetadataTests(unittest.TestCase):
    def test_png_static_and_animated(self):
        header = png()
        control = chunk(b"acTL", pack(">II", 23, 7))
        idat = chunk(b"IDAT", b"")
        for tail, frames in [(control, 23), (idat + control, 1), (control[:-1], None),
                             (chunk(b"IEND", b"") + control, None), (b"", None),
                             (chunk(b"acTL", bytes(4)), None), (chunk(b"acTL", bytes(8)), None),
                             (chunk(b"acTL", pack(">II", 2**31, 1)), None)]:
            with self.subTest(tail=tail):
                self.assertEqual(probe(header + tail), ImageInfo(ImageFormat.PNG, 23, 11, 8, frames))

    def test_png_ihdr_validation(self):
        for data in (png()[:24], png().replace(b"IHDR", b"tEXt"), png().replace(b"\x0dIHDR", b"\x0cIHDR")):
            self.assertEqual(probe(data), ImageInfo(ImageFormat.PNG))
        self.assertEqual(probe(png(0, 2**31, 0)), ImageInfo(ImageFormat.PNG))

    def test_encoded_apng_file(self):
        path = Path(__file__).parent / "data" / "animation.png"
        self.assertEqual(probe(path), ImageInfo(ImageFormat.PNG, 12, 7, 8, 2))

    def test_skips_payloads_beyond_detection_buffer(self):
        png_data = png() + chunk(b"tEXt", bytes(300)) + chunk(b"acTL", pack(">II", 2, 0))
        jpg_data = (b"\xff\xd8" + jpeg_segment(0xe1, bytes(300))
                    + jpeg_segment(0xc4, b"\xff\xc0") + jpeg_segment(0xc2, pack(">BHH", 12, 11, 23)))
        for data, expected in [(png_data, ImageInfo(ImageFormat.PNG, 23, 11, 8, 2)),
                               (jpg_data, ImageInfo(ImageFormat.JPEG, 23, 11, 12, 1))]:
            for source in (data, memoryview(data), io.BytesIO(data), ShortStream(data)):
                with self.subTest(format=expected.format, source=type(source)):
                    self.assertEqual(probe(source), expected)
            for stream in (io.BytesIO(data[:140]), ShortStream(data[:140])):
                self.assertIsNone(probe(stream).frame_count)

    def test_jpeg_markers(self):
        sof = jpeg_segment(0xc0, pack(">BHH", 8, 0, 1024))
        header = b"\xff\xd8"
        self.assertEqual(probe(header + b"\xff\xff\xd1\xff\x01" + sof),
                         ImageInfo(ImageFormat.JPEG, 1024, None, 8, 1))
        for tail in (b"\xff", sof[:-1], b"\xff\xe0\x00\x01" + sof, jpeg_segment(0xda, b"") + sof):
            self.assertEqual(probe(header + tail), ImageInfo(ImageFormat.JPEG))

    def test_bmp_header_variants(self):
        for size in (12, 16, 40, 52, 56, 64, 108, 124):
            with self.subTest(size=size):
                data = bmp(size, 300, 200 if size == 12 else -200, 24)
                self.assertEqual(probe(data), ImageInfo(ImageFormat.BMP, 300, 200, 24, 1))
                self.assertEqual(probe(data[:-1]), ImageInfo(ImageFormat.BMP))
        self.assertEqual(probe(bmp(40, -1, -2**31, 0)), ImageInfo(ImageFormat.BMP, frame_count=1))

    def test_psd_dimensions_and_precision(self):
        header = b"8BPS" + pack(">H6xH", 1, 3)
        self.assertEqual(probe(header + pack(">IIH", 400, 500, 16)), ImageInfo(ImageFormat.PSD, 500, 400, 16, 1))
        self.assertEqual(probe(header + pack(">IIH", 400, 2**31, 0)), ImageInfo(ImageFormat.PSD, None, 400, None, 1))
        self.assertEqual(probe(header + bytes(9)), ImageInfo(ImageFormat.PSD))

    def test_wmf_96_dpi_and_standard_header(self):
        self.assertEqual(probe(wmf(3000, 1500, 0, 0, 1440)), ImageInfo(ImageFormat.WMF, 200, 100, None, 1))
        self.assertEqual(probe(wmf(-20, 0, 3, 0, 100)), ImageInfo(ImageFormat.WMF, 22, None, None, 1))
        for data in (wmf(0, 0, 1, 1, 0), wmf(0, 0, 1, 1, 1)[:-1], pack("<3H", 1, 9, 0x300) + bytes(10)):
            self.assertEqual(probe(data), ImageInfo(ImageFormat.WMF))
