import io
import unittest
from pathlib import Path
from struct import pack

from aspose_imaging_foss import ImageFormat, ImageInfo, probe
from test_probe import ShortStream


DICM = bytes(128) + b"DICM"
IMPLICIT_LE = b"1.2.840.10008.1.2"
EXPLICIT_LE = b"1.2.840.10008.1.2.1"
EXPLICIT_BE = b"1.2.840.10008.1.2.2"


def meta_uid(uid):
    value = uid + bytes(len(uid) % 2)
    return pack("<HH2sH", 2, 0x10, b"UI", len(value)) + value


def ushort(element, value, syntax=EXPLICIT_LE):
    order = ">" if syntax == EXPLICIT_BE else "<"
    length = pack("<I", 2) if syntax == IMPLICIT_LE else b"US" + pack(order + "H", 2)
    return pack(order + "HH", 0x28, element) + length + pack(order + "H", value)


def box(kind, data, extended=False):
    if extended:
        return pack(">I4sQ", 1, kind, 16 + len(data)) + data
    return pack(">I4s", 8 + len(data), kind) + data


def ispe(width, height):
    return box(b"ispe", pack(">III", 0, width, height))


def pitm(item, version):
    size = 4 if version else 2
    return box(b"pitm", bytes([version, 0, 0, 0]) + item.to_bytes(size, "big"))


def ipma(item, version, wide, properties):
    size = 4 if version else 2
    data = pack(">B3xI", version, 2)
    if wide:
        data = data[:3] + b"\x01" + data[4:]
    # A different item's association precedes the primary item's associations.
    for item_id, indices in [(1, [1]), (item, properties)]:
        data += item_id.to_bytes(size, "big") + bytes([len(indices)])
        for index in indices:
            data += pack(">H", index | 0x8000) if wide else bytes([index | 0x80])
    return box(b"ipma", data)


def heif(properties, primary=b"", associations=b"", brand=b"heic", compatible=b"", extended=False):
    ftyp = box(b"ftyp", brand + bytes(4) + compatible)
    iprp = box(b"iprp", box(b"ipco", properties) + associations, extended)
    return ftyp + box(b"meta", bytes(4) + primary + iprp, extended)


class ContainerTests(unittest.TestCase):
    def assert_sources(self, data, expected):
        for source in (data, io.BytesIO(data), ShortStream(data)):
            with self.subTest(source=type(source)):
                self.assertEqual(probe(source), expected)

    def test_dicom_transfer_syntaxes(self):
        for syntax in (EXPLICIT_LE, IMPLICIT_LE, EXPLICIT_BE, b"1.2.840.10008.1.2.4.50"):
            # File meta is little-endian even when the dataset is big-endian.
            other_meta = pack("<HH2sHI", 2, 1, b"OB", 0, 2) + bytes(2)
            dataset = b"".join(ushort(tag, value, syntax) for tag, value in
                               [(0x100, 16), (0x10, 257), (0x11, 513)])
            with self.subTest(syntax=syntax):
                self.assert_sources(DICM + meta_uid(syntax) + other_meta + dataset,
                                    ImageInfo(ImageFormat.DICOM, 513, 257, 16))
        self.assert_sources(DICM + ushort(0x11, 123), ImageInfo(ImageFormat.DICOM, width=123))

    def test_dicom_partial_and_stopping_tags(self):
        rows = ushort(0x10, 17)
        rest = ushort(0x11, 19) + ushort(0x100, 8)
        for boundary in (pack("<HH", 0x7fe0, 0x10),
                         pack("<HH2sHI", 8, 1, b"SQ", 0, 0xffff_ffff)):
            self.assert_sources(DICM + rows + boundary + rest, ImageInfo(ImageFormat.DICOM, height=17))
        self.assert_sources(DICM + rows + rest[:-1], ImageInfo(ImageFormat.DICOM, 19, 17))
        self.assert_sources(DICM + meta_uid(b"A" * 65) + rows, ImageInfo(ImageFormat.DICOM))
        skipped = pack("<HH2sHI", 8, 1, b"OB", 0, 200) + bytes(200)
        self.assert_sources(DICM + skipped + rows + rest, ImageInfo(ImageFormat.DICOM, 19, 17, 8))

    def test_dicom_stops_before_pixel_payload(self):
        dimensions = ushort(0x10, 8) + ushort(0x11, 0) + ushort(0x100, 16)
        with ShortStream(DICM + dimensions + bytes(256)) as stream:
            self.assertEqual(probe(stream), ImageInfo(ImageFormat.DICOM, None, 8, 16))
            self.assertEqual(stream.tell(), len(DICM) + len(dimensions))

    def test_encoded_primary_images(self):
        directory = Path(__file__).parent / "data"
        self.assertEqual(probe(directory / "primary.avif"), ImageInfo(ImageFormat.AVIF, 73, 41))
        # libheif encodes a 74x64 image and crops it to 73x41 with a clap property.
        self.assertEqual(probe(directory / "cropped.heic"), ImageInfo(ImageFormat.HEIC, 74, 64))

    def test_heif_primary_dimensions(self):
        props = box(b"hvcC", bytes(4)) + ispe(1024, 768) + ispe(37, 19)
        for version in (0, 1):
            item = 7 if version == 0 else 0x12345
            for wide in (False, True):
                associations = ipma(item, version, wide, [1, 3, 2])
                for brand, compatible, fmt in [(b"heic", b"avif", ImageFormat.HEIC),
                                                (b"mif1", b"avif", ImageFormat.AVIF)]:
                    with self.subTest(version=version, wide=wide, brand=brand):
                        data = heif(props, pitm(item, version), associations, brand, compatible, extended=True)
                        self.assert_sources(data, ImageInfo(fmt, 37, 19))

    def test_wide_property_index_and_multiple_associations(self):
        props = ispe(200, 100) + box(b"free", b"") * 128 + ispe(11, 7)
        associations = ipma(9, 0, True, [2]) + ipma(9, 0, True, [130])
        data = heif(props, pitm(9, 0), associations)
        self.assert_sources(data, ImageInfo(ImageFormat.HEIC, 11, 7))

    def test_heif_missing_or_incomplete_association(self):
        props = ispe(20, 10) + ispe(30, 20) + ispe(10, 60)
        # The last property wins the tie for largest area.
        for associations in (b"", ipma(3, 0, False, [1]), ipma(4, 0, False, [1])[:-1]):
            self.assert_sources(heif(props, pitm(4, 0), associations), ImageInfo(ImageFormat.HEIC, 10, 60))
        self.assert_sources(heif(ispe(2**31, 12), brand=b"avif"), ImageInfo(ImageFormat.AVIF, None, 12))

    def test_heif_box_boundaries(self):
        prefix = box(b"ftyp", b"avif" + bytes(4))
        outside = box(b"iprp", box(b"ipco", ispe(48, 24)))
        self.assert_sources(prefix + box(b"meta", bytes(4)) + outside, ImageInfo(ImageFormat.AVIF))
        self.assert_sources(prefix + pack(">I4s", 0, b"meta") + bytes(4) + outside,
                            ImageInfo(ImageFormat.AVIF, 48, 24))
        for data in (heif(ispe(48, 24))[:-1], prefix + pack(">I4sQ", 1, b"meta", 15)):
            expected = ImageFormat.AVIF if data.startswith(prefix) else ImageFormat.HEIC
            self.assert_sources(data, ImageInfo(expected))
