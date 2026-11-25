import io
import struct
import tempfile
import unittest
from pathlib import Path

from aspose_imaging_foss import ImageFormat, ImageInfo, detect_format, probe


def emf(left=0, top=0, right=799, bottom=599):
    header = bytearray(44)
    struct.pack_into("<II4i", header, 0, 1, 88, left, top, right, bottom)
    header[40:44] = b" EMF"
    return bytes(header)


def ftyp(major, compatible=b"", size=0):
    return struct.pack(">I4s4sI", size, b"ftyp", major, 0) + compatible


def samples():
    return [
        (ImageFormat.PNG, b"\x89PNG\r\n\x1a\n"),
        (ImageFormat.JPEG, b"\xff\xd8\xff"),
        (ImageFormat.GIF, b"GIF87a"),
        (ImageFormat.GIF, b"GIF89a"),
        (ImageFormat.BMP, b"BM" + bytes(12) + struct.pack("<I", 40)),
        (ImageFormat.WEBP, b"RIFF" + bytes(4) + b"WEBP"),
        (ImageFormat.ICO, b"\x00\x00\x01\x00"),
        (ImageFormat.TIFF, b"II*\0"),
        (ImageFormat.TIFF, b"MM\0*"),
        (ImageFormat.PSD, b"8BPS"),
        (ImageFormat.EMF, emf()),
        (ImageFormat.WMF, bytes.fromhex("9a c6 cd d7 00 00")),
        (ImageFormat.WMF, struct.pack("<3H", 2, 9, 0x0300)),
        (ImageFormat.DICOM, bytes(128) + b"DICM"),
        (ImageFormat.DJVU, b"AT&TFORM"),
        (ImageFormat.AVIF, ftyp(b"avis")),
        (ImageFormat.AVIF, ftyp(b"mif1", b"avif")),
        (ImageFormat.HEIC, ftyp(b"heic", b"avif")),
        (ImageFormat.HEIC, ftyp(b"mif2", b"avif", size=16)),
    ]


class ShortStream(io.BytesIO):
    def seekable(self):
        return False

    def seek(self, *args):
        raise io.UnsupportedOperation("seek")

    def read(self, size=-1):
        return super().read(min(size, 3))


class ProbeTests(unittest.TestCase):
    def test_signatures(self):
        for expected, data in samples():
            with self.subTest(expected=expected, data=data):
                self.assertEqual(detect_format(data), expected)
                self.assertEqual(probe(data).format, expected)

    def test_incomplete_and_invalid_signatures(self):
        for data in (b"", b"GIF89", emf()[:-1], bytes(128) + b"DIC",
                     bytes.fromhex("9a c6 cd d7"), b"BM" + bytes(16),
                     ftyp(b"isom", b"xavif"), b"RIFF" + bytes(8)):
            with self.subTest(data=data):
                self.assertEqual(probe(data), ImageInfo(ImageFormat.UNKNOWN))

    def test_emf_bounds(self):
        for bounds, width, height in [
            ((0, 0, 799, 599), 800, 600),
            ((5, 5, 5, 5), 1, 1),
            ((-5, -7, 4, 2), 10, 10),
            ((0, 0, -1, -1), None, None),
            ((-2**31, 0, 2**31 - 1, 9), None, 10),
        ]:
            with self.subTest(bounds=bounds):
                self.assertEqual(probe(emf(*bounds)), ImageInfo(ImageFormat.EMF, width, height, None, 1))

    def test_reads_seekable_stream_from_start(self):
        with io.BytesIO(emf()) as stream:
            stream.seek(17)
            self.assertEqual(probe(stream), probe(emf()))
            self.assertFalse(stream.closed)

    def test_non_seekable_short_reads(self):
        data = bytes(128) + b"DICM"
        with ShortStream(data + bytes(200)) as stream:
            self.assertEqual(detect_format(stream), ImageFormat.DICOM)
            self.assertEqual(stream.tell(), 132)
        with ShortStream(emf()) as stream:
            self.assertEqual(probe(stream), probe(emf()))

    def test_paths_and_buffers(self):
        data = emf()
        expected = ImageInfo(ImageFormat.EMF, 800, 600, None, 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.bin"
            path.write_bytes(data)
            for source in (path, str(path), bytearray(data), memoryview(data)):
                self.assertEqual(detect_format(source), ImageFormat.EMF)
                self.assertEqual(probe(source), expected)
