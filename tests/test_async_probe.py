import asyncio
import io
from pathlib import Path
from struct import pack
import tempfile
import threading
import unittest
from unittest.mock import patch

from aspose_imaging_foss import ImageFormat, ImageInfo, probe, probe_async
from test_containers import DICM, ushort
from test_metadata import bmp, chunk, jpeg_segment, png, wmf
from test_multiframe import document, gif_frame, gif_screen, iff, page, tiff
from test_probe import emf


def format_samples():
    files = Path(__file__).parent / "data"
    return [
        (ImageFormat.PNG, (files / "animation.png").read_bytes()),
        (ImageFormat.JPEG, b"\xff\xd8" + jpeg_segment(0xc0, pack(">BHH", 8, 11, 23))),
        (ImageFormat.GIF, gif_screen() + gif_frame() * 2 + b";"),
        (ImageFormat.BMP, bmp(40, 300, -200, 24)),
        (ImageFormat.WEBP, (files / "animation.webp").read_bytes()),
        (ImageFormat.ICO, pack("<HHHBBBBHH", 0, 1, 3, 0, 16, 0, 0, 1, 32)),
        (ImageFormat.TIFF, tiff(">")),
        (ImageFormat.PSD, b"8BPS" + pack(">H6xHIIH", 1, 3, 24, 32, 16)),
        (ImageFormat.EMF, emf(-1, 2, 3, 4)),
        (ImageFormat.WMF, wmf(3000, 1500, 0, 0, 1440)),
        (ImageFormat.DICOM, DICM + ushort(0x10, 257) + ushort(0x11, 513) + ushort(0x100, 16)),
        (ImageFormat.DJVU, document(iff(b"DIRM", b"\x80"), page(72, 48), page(90, 60))),
        (ImageFormat.AVIF, (files / "primary.avif").read_bytes()),
        (ImageFormat.HEIC, (files / "cropped.heic").read_bytes()),
    ]


class AsyncFile:
    def __init__(self, data):
        self.file = io.BytesIO(data)
        self.bytes_read = 0

    async def read(self, size):
        await asyncio.sleep(0)
        data = self.file.read(min(7, size))
        self.bytes_read += len(data)
        return data

    async def seek(self, offset, whence=0):
        return self.file.seek(offset, whence)

    async def tell(self):
        return self.file.tell()


class AsyncProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_streams_match_sync_across_formats(self):
        for image_format, data in format_samples():
            with self.subTest(image_format=image_format):
                expected = probe(data)
                self.assertEqual(expected.format, image_format)
                seekable = AsyncFile(data)
                await seekable.seek(len(data))
                pipe = asyncio.StreamReader()
                pipe.feed_data(b"prefix" + data)
                pipe.feed_eof()
                self.assertEqual(await pipe.read(6), b"prefix")
                for source in (seekable, pipe, memoryview(data)):
                    self.assertEqual(await probe_async(source), expected)
                self.assertFalse(seekable.file.closed)

    async def test_only_header_is_read_from_seekable_png(self):
        data = png() + chunk(b"IDAT", bytes(1024 * 1024))
        stream = AsyncFile(data)
        self.assertEqual(await probe_async(stream), ImageInfo(ImageFormat.PNG, 23, 11, 8, 1))
        self.assertLessEqual(stream.bytes_read, 132)

    async def test_path_and_blocking_stream(self):
        data = tiff("<")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "picture.tif"
            path.write_bytes(data)
            results = await asyncio.gather(probe_async(path), probe_async(io.BytesIO(data)))
            self.assertEqual(results, [probe(data)] * 2)
            with self.assertRaises(FileNotFoundError):
                await probe_async(path.with_suffix(".missing"))

    async def test_pre_cancelled_probe_leaves_source_untouched(self):
        stream = AsyncFile(b"abc")
        await stream.seek(2)

        async def cancel_before_probe(source):
            asyncio.current_task().cancel()
            await probe_async(source)

        with patch("builtins.open") as opened:
            for source in (stream, "missing.png"):
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.create_task(cancel_before_probe(source))
            opened.assert_not_called()
        self.assertEqual(await stream.tell(), 2)

    async def test_cancellation_interrupts_async_metadata_read(self):
        started = asyncio.Event()
        finished = asyncio.Event()

        class WaitingStream(AsyncFile):
            async def read(self, size):
                if self.file.tell() >= 132:
                    started.set()
                    try:
                        await asyncio.Future()
                    finally:
                        finished.set()
                return await super().read(size)

        stream = WaitingStream(DICM + ushort(0x10, 42))
        task = asyncio.create_task(probe_async(stream))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(finished.is_set())
        self.assertFalse(stream.file.closed)

    async def test_stream_error_propagates(self):
        error = OSError("metadata read failed")

        class FailingStream(AsyncFile):
            async def read(self, size):
                if self.file.tell() >= 132:
                    raise error
                return await super().read(size)

        with self.assertRaises(OSError) as raised:
            await probe_async(FailingStream(DICM + ushort(0x10, 42)))
        self.assertIs(raised.exception, error)

    async def test_blocking_read_finishes_before_cancellation_returns(self):
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()

        class BlockingReader(io.BytesIO):
            calls = 0

            def read(self, size):
                self.calls += 1
                loop.call_soon_threadsafe(started.set)
                if not release.wait(3):
                    raise TimeoutError("read was not released")
                return super().read(size)

        stream = BlockingReader(DICM + ushort(0x10, 42))
        task = asyncio.create_task(probe_async(stream))
        try:
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            task.cancel()
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(stream.calls, 1)
        self.assertEqual(stream.tell(), 132)
        self.assertFalse(stream.closed)

    async def test_buffered_scan_yields_for_cancellation(self):
        data = gif_screen() + gif_frame() * 5000
        task = asyncio.create_task(probe_async(data))
        await asyncio.sleep(0)  # Initial checkpoint.
        await asyncio.sleep(0)  # The parser is now scanning the buffer.
        self.assertFalse(task.done())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
