"""Границы и сохранность графических форматов на независимых миниатюрах."""

import struct
import unittest
import zlib

from smuta_build.graphics import decode_bmp, decode_palette, decode_spr, indexed_png


def bmp(width=3, height=2):
    stride = (width + 3) & ~3
    pixels = b"".join(bytes(v % 256 for v in range(y * width, (y + 1) * width)) + b"X" * (stride - width)
                      for y in range(abs(height)))
    palette = b"".join(bytes((i, 0, 255 - i, 123)) for i in range(256))
    return (struct.pack("<2sIHHI", b"BM", 1078 + len(pixels), 0, 0, 1078)
            + struct.pack("<IiiHHIIiiII", 40, width, height, 1, 8, 0, len(pixels), 0, 0, 256, 0)
            + palette + pixels)


class GraphicsTests(unittest.TestCase):
    def test_bmp_orientation_padding_and_channels(self):
        image = decode_bmp(bmp())
        self.assertEqual(image.pixels, bytes((3, 4, 5, 0, 1, 2)))
        self.assertEqual(image.palette[:6], bytes((255, 0, 0, 254, 0, 1)))
        self.assertEqual(decode_bmp(bmp(height=-2)).pixels, bytes(range(6)))
        self.assertFalse(image.metadata["bfSize_anomaly"])

    def test_bmp_rejects_invalid_ranges_and_formats(self):
        data = bmp()
        for short in (b"", data[:53], data[:1077], data[:-1]):
            with self.assertRaises(ValueError):
                decode_bmp(short, allow_smuta_bfsize=True)
        for offset, form, value in ((2, "I", 32539), (10, "I", 54), (14, "I", 108),
                                    (18, "i", 0), (22, "i", 0), (26, "H", 2),
                                    (28, "H", 24), (30, "I", 1), (34, "I", 999),
                                    (46, "I", 257)):
            broken = bytearray(data)
            struct.pack_into("<" + form, broken, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                decode_bmp(bytes(broken), allow_smuta_bfsize=True)

    def test_only_exact_known_bfsize_exception(self):
        data = bytearray(bmp(320, 200))
        struct.pack_into("<I", data, 2, 32539)
        with self.assertRaises(ValueError):
            decode_bmp(bytes(data))
        self.assertTrue(decode_bmp(bytes(data), allow_smuta_bfsize=True).metadata["bfSize_anomaly"])
        data.append(0)
        with self.assertRaises(ValueError):
            decode_bmp(bytes(data), allow_smuta_bfsize=True)

    def test_spr_retains_header_and_pixels_without_semantic_defaults(self):
        header = struct.pack("<HH", 3, 2) + bytes(range(14))
        data = header + bytes((0, 1, 2, 3, 4, 255))
        image = decode_spr(data)
        self.assertEqual(image.pixels, data[18:])
        self.assertEqual(bytes.fromhex(image.metadata["header_hex"]), header)
        self.assertIsNone(image.metadata["anchor"])
        self.assertIsNone(image.metadata["transparent_index"])
        for broken in (data[:17], data[:-1], data + b"X", b"\0" * 18):
            with self.assertRaises(ValueError):
                decode_spr(broken)

    def test_palette_and_png_preserve_all_indices(self):
        raw = bytes(range(64)) * 12
        palette = decode_palette(raw)
        self.assertEqual(palette, bytes(v * 4 for v in raw))
        for invalid in (raw[:-1], raw + b"\0", b"\xff" * 768):
            with self.assertRaises(ValueError):
                decode_palette(invalid)
        image = decode_spr(struct.pack("<HH", 16, 16) + b"\0" * 14 + bytes(range(256)))
        png = indexed_png(image, palette)
        pos, chunks = 8, {}
        while pos < len(png):
            length, = struct.unpack_from(">I", png, pos)
            kind, payload = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + length]
            self.assertEqual(zlib.crc32(kind + payload), struct.unpack_from(">I", png, pos + 8 + length)[0])
            chunks[kind] = payload
            pos += 12 + length
        self.assertNotIn(b"tRNS", chunks)
        self.assertEqual(chunks[b"PLTE"], palette)
        rows = zlib.decompress(chunks[b"IDAT"])
        self.assertEqual(b"".join(rows[y * 17 + 1:(y + 1) * 17] for y in range(16)), image.pixels)


if __name__ == "__main__":
    unittest.main()
