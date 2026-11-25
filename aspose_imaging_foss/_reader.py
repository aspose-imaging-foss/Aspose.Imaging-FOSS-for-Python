from contextlib import contextmanager
from io import SEEK_END
from shutil import copyfileobj
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Iterator


def read_up_to(stream: BinaryIO, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        part = stream.read(size - len(data))
        if not part:
            break
        data.extend(part)
    return bytes(data)


class HeaderReader:
    """Read forward from buffered bytes, then continue on the original stream."""

    def __init__(self, buffered: memoryview, stream: BinaryIO | None):
        self.buffered = buffered
        self.stream = stream
        self.end: int | None = None

    def read(self, size: int) -> bytes:
        data = bytes(self.buffered[:size])
        self.buffered = self.buffered[len(data):]
        if len(data) < size and self.stream is not None:
            data += read_up_to(self.stream, size - len(data))
        return data

    def skip(self, size: int) -> bool:
        buffered = min(len(self.buffered), size)
        self.buffered = self.buffered[buffered:]
        remaining = size - buffered
        if not remaining:
            return True
        if self.stream is None:
            return False
        if self.stream.seekable():
            position = self.stream.tell()
            if self.end is None:
                self.end = self.stream.seek(0, SEEK_END)
            target = position + remaining
            self.stream.seek(min(target, self.end))
            return target <= self.end

        while remaining:
            chunk = self.stream.read(min(remaining, 65536))
            if not chunk:
                return False
            remaining -= len(chunk)
        return True


class OffsetReader:
    """Read container metadata by absolute file offset."""

    def __init__(self, buffered: memoryview, stream: BinaryIO | None):
        self._buffered = buffered
        self._stream = stream
        self.length = len(buffered) if stream is None else stream.seek(0, SEEK_END)

    def read_at(self, offset: int, size: int) -> bytes:
        if offset >= self.length:
            return b""
        if self._stream is None or offset + size <= len(self._buffered):
            return bytes(self._buffered[offset:offset + size])
        self._stream.seek(offset)
        return read_up_to(self._stream, size)


@contextmanager
def offset_reader(buffered: memoryview, stream: BinaryIO | None) -> Iterator[OffsetReader]:
    if stream is None or stream.seekable():
        yield OffsetReader(buffered, stream)
        return
    # Preserve offsets relative to the start of this image on a forward-only stream.
    with SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as spool:
        spool.write(buffered)
        copyfileobj(stream, spool)
        yield OffsetReader(buffered, spool)
