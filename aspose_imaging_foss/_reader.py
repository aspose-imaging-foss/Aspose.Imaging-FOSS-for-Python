import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from inspect import isawaitable, iscoroutine, iscoroutinefunction
from io import SEEK_END
from tempfile import SpooledTemporaryFile
from typing import Any, AsyncIterator, BinaryIO, Protocol, TypeVar


class AsyncReadable(Protocol):
    def read(self, size: int, /) -> Awaitable[bytes]: ...


Stream = BinaryIO | AsyncReadable
T = TypeVar("T")


def run_inline(operation: Coroutine[Any, Any, T]) -> T:
    """With inline I/O the parser completes on its first resume, even inside a running loop."""
    try:
        operation.send(None)
    except StopIteration as result:
        return result.value
    finally:
        operation.close()
    raise RuntimeError("Synchronous probe unexpectedly suspended")


class ProbeIO:
    """Run the same parsers with inline I/O or asyncio-aware I/O."""

    def __init__(self, asynchronous: bool):
        self.asynchronous = asynchronous
        self._operations = 0

    async def checkpoint(self) -> None:
        # Buffered reads may complete without yielding, even for an async stream.
        if self.asynchronous:
            self._operations += 1
            if self._operations % 256 == 0:
                await asyncio.sleep(0)

    async def call(self, method: Callable, *args):
        if not self.asynchronous:
            return method(*args)
        if iscoroutinefunction(method):
            return await method(*args)
        # Blocking file methods run off the event loop. Finish an in-flight call
        # before cancellation returns the stream to its owner.
        pending = asyncio.get_running_loop().run_in_executor(None, method, *args)
        try:
            result = await asyncio.shield(pending)
        except asyncio.CancelledError:
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not pending.cancelled() and pending.exception() is None:
                abandoned = pending.result()
                if iscoroutine(abandoned):
                    abandoned.close()
            raise
        if isawaitable(result):
            return await result
        return result

    async def seekable(self, stream: Stream) -> bool:
        method = getattr(stream, "seekable", None)
        if method is not None:
            return await self.call(method)
        return hasattr(stream, "seek") and hasattr(stream, "tell")

    async def read_up_to(self, stream: Stream, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            await self.checkpoint()
            part = await self.call(stream.read, size - len(data))
            if not part:
                break
            data.extend(part)
        return bytes(data)


class HeaderReader:
    """Read forward from buffered bytes, then continue on the original stream."""

    def __init__(self, buffered: memoryview, stream: Stream | None, io: ProbeIO):
        self.buffered = buffered
        self.stream = stream
        self.io = io
        self.end: int | None = None

    async def read(self, size: int) -> bytes:
        await self.io.checkpoint()
        data = bytes(self.buffered[:size])
        self.buffered = self.buffered[len(data):]
        if len(data) < size and self.stream is not None:
            data += await self.io.read_up_to(self.stream, size - len(data))
        return data

    async def skip(self, size: int) -> bool:
        await self.io.checkpoint()
        buffered = min(len(self.buffered), size)
        self.buffered = self.buffered[buffered:]
        remaining = size - buffered
        if not remaining:
            return True
        if self.stream is None:
            return False
        if await self.io.seekable(self.stream):
            position = await self.io.call(self.stream.tell)
            if self.end is None:
                self.end = await self.io.call(self.stream.seek, 0, SEEK_END)
            target = position + remaining
            await self.io.call(self.stream.seek, min(target, self.end))
            return target <= self.end

        while remaining:
            await self.io.checkpoint()
            chunk = await self.io.call(self.stream.read, min(remaining, 65536))
            if not chunk:
                return False
            remaining -= len(chunk)
        return True


class OffsetReader:
    """Read container metadata by absolute file offset."""

    def __init__(self, buffered: memoryview, stream: Stream | None, length: int, io: ProbeIO):
        self._buffered = buffered
        self._stream = stream
        self.length = length
        self._io = io

    async def read_at(self, offset: int, size: int) -> bytes:
        await self._io.checkpoint()
        if offset >= self.length:
            return b""
        if self._stream is None or offset + size <= len(self._buffered):
            return bytes(self._buffered[offset:offset + size])
        await self._io.call(self._stream.seek, offset)
        return await self._io.read_up_to(self._stream, size)


@asynccontextmanager
async def offset_reader(buffered: memoryview, stream: Stream | None, io: ProbeIO) -> AsyncIterator[OffsetReader]:
    if stream is None:
        yield OffsetReader(buffered, None, len(buffered), io)
    elif await io.seekable(stream):
        length = await io.call(stream.seek, 0, SEEK_END)
        yield OffsetReader(buffered, stream, length, io)
    else:
        # Preserve image-relative offsets on forward-only streams.
        spool = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
        try:
            length = await io.call(spool.write, buffered)
            while True:
                chunk = await io.read_up_to(stream, 65536)
                if not chunk:
                    break
                length += await io.call(spool.write, chunk)
            yield OffsetReader(buffered, spool, length, io)
        finally:
            await io.call(spool.close)
