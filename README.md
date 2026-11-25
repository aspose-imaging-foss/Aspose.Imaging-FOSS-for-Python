# Aspose.Imaging FOSS for Python

Aspose.Imaging FOSS identifies image formats and reads image metadata from files, byte buffers, and binary streams. It runs on Python 3.10 or later and uses only the standard library. The reference for this port is commit `0f8899ff8372f6fd5b0d374a1c6b532b7c4cf92f` of the Aspose.Imaging.Foss C# library.

## Format detection and metadata

Use `detect_format()` to identify an image from its signature. It reads up to 132 bytes and returns `ImageFormat.UNKNOWN` for an unrecognized signature. Use `probe()` to retrieve the format and available metadata. It reads beyond the signature when needed. If a header is truncated, the result keeps the detected format and any metadata already read. Unavailable fields are `None`.

| Format | Metadata returned by `probe()` |
| --- | --- |
| PNG, APNG | Image dimensions and bit depth from the header. For APNG, the frame count comes from the `acTL` chunk before the first `IDAT` chunk. |
| JPEG, BMP, PSD | Image dimensions and bit depth from the header. |
| GIF, WebP | Canvas dimensions and frame count. |
| ICO | Dimensions and bit depth of the first image, plus the number of images in the icon directory. |
| TIFF | Dimensions of the first page, bit depth per channel, and the number of pages in the image file directory (IFD) chain. |
| EMF | Pixel dimensions calculated from the inclusive bounding rectangle. |
| WMF | Pixel dimensions at 96 DPI for placeable WMF files. Standard WMF headers provide format identification only. |
| DICOM | Width from `Columns`, height from `Rows`, and bit depth from `BitsAllocated`. The reader supports Implicit VR Little Endian, Explicit VR Little Endian, and Explicit VR Big Endian, including metadata preceding JPEG-compressed pixel data. It stops at `PixelData` or an element with undefined length. |
| DjVu | Page dimensions and the number of embedded pages in a complete bundled DJVM document. The page count is `None` when pages are stored in external files. |
| AVIF, HEIC | Primary image dimensions from the `ispe` property, using `pitm` to identify the primary item and `ipma` to find its properties. If the association is unavailable, the reader uses the `ispe` with the largest area. As in the C# library, these dimensions precede any `clap` cropping. |

Both functions accept a path, a byte buffer, or a binary stream. They read seekable streams from the beginning and non-seekable streams from the current position. Caller-provided streams stay open. When probing TIFF, DjVu, AVIF, or HEIC from a non-seekable stream, the reader buffers the input and uses a temporary file if it exceeds 1 MiB.

```python
from aspose_imaging_foss import ImageFormat, detect_format, probe

assert detect_format(b"GIF89a") == ImageFormat.GIF

info = probe("drawing.emf")
print(info.width, info.height, info.frame_count)
```

## Installation and testing

From the project root, run these commands in a virtual environment.

```sh
python -m pip install .
python -m unittest discover -s tests -v
```

## Porting progress

1. **Complete — package and format detection.** Ported the result model, detection for all 14 formats, and the EMF metadata reader. Verified paths, byte buffers, short reads, and non-seekable streams.
2. **Complete — raster formats and WMF.** Ported metadata readers for PNG/APNG, JPEG, BMP, PSD, and WMF. Verified header variants, APNG frame counts, and reads beyond the first 132 bytes.
3. **Complete — animation and multipage images.** Ported GIF, WebP, ICO, TIFF, and DjVu. Verified dimensions, bit depth, and frame, image, and page counts against the C# library.
4. **Complete — DICOM, AVIF, and HEIC.** Ported metadata readers for all three formats. Verified DICOM byte ordering and primary image selection in AVIF and HEIC.
5. **Planned — asynchronous reading and final verification.** Port the asynchronous API and cancellation support. Compare all result fields against the C# library using the same valid, truncated, and malformed inputs. Verify installation of the built package.
