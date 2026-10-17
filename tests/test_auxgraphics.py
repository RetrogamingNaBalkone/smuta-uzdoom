"""Проверки диапазонов GFX-03 и восстановленных правил EXE."""

import struct
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from smuta_build.auxgraphics import character_map, decode_font, decode_light, direct_colors, glyph_index


class AuxiliaryGraphicsTests(unittest.TestCase):
    def test_font_rows_bits_and_glyph_boundaries(self):
        for height in (8, 16):
            data = bytearray(256 * height)
            data[65 * height] = 0x81
            data[65 * height + height - 1] = 0x40
            glyphs = decode_font(bytes(data))
            self.assertEqual(len(glyphs), 256)
            self.assertEqual(glyphs[65].pixels[:8], b"\1\0\0\0\0\0\0\1")
            self.assertEqual(glyphs[65].pixels[-8:], b"\0\1\0\0\0\0\0\0")
            self.assertFalse(any(glyphs[64].pixels + glyphs[66].pixels))

    def test_font_invalid_lengths(self):
        for size in (0, 2047, 2049, 4095, 4097):
            with self.assertRaises(ValueError):
                decode_font(bytes(size))

    def test_character_map_cyrillic_and_specials(self):
        for char in "АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдежзийклмнопрстуфхцчшщъыьэюя":
            self.assertEqual(glyph_index(char.encode("cp1251")[0]), char.encode("cp866")[0])
        self.assertEqual([glyph_index(c) for c in (0, 127, 128, 239, 240, 255)], [0, 127, 64, 175, 224, 239])
        mapping = character_map()
        self.assertEqual(mapping[168]["glyph"], ord("h"))
        self.assertFalse(mapping[168]["unicode_verified"])
        self.assertIsNone(mapping[152]["cp1251"])
        self.assertEqual(mapping[36]["text_control"], "end")
        for value in (-1, 256):
            with self.assertRaises(ValueError):
                glyph_index(value)

    def test_light_sections_and_asymmetric_blend(self):
        data = bytearray(90112)
        data[31 * 256 + 255] = 123
        struct.pack_into("<H", data, 8192 + 2 * (31 * 256 + 255), 0x7fff)
        data[24576 + 7 * 256 + 9] = 42
        light = decode_light(bytes(data))
        self.assertEqual(light.shade(255, 31), 123)
        self.assertEqual(light.rgb555[-1], 0x7fff)
        self.assertEqual(light.mix(7, 9), 42)
        self.assertEqual(light.mix(9, 7), 0)
        for args in ((-1, 0), (256, 0), (0, -1), (0, 32)):
            with self.assertRaises(ValueError):
                light.shade(*args)
        for args in ((-1, 0), (256, 0), (0, -1), (0, 256)):
            with self.assertRaises(ValueError):
                light.mix(*args)

    def test_light_corruption(self):
        for size in (0, 8192, 24576, 90111, 90113):
            with self.assertRaises(ValueError):
                decode_light(bytes(size))
        data = bytearray(90112)
        data[8193] = 128
        with self.assertRaises(ValueError):
            decode_light(bytes(data))

    def test_palette_integer_rounding_555_and_565(self):
        palette = bytes([63, 63, 63]) * 256
        a = direct_colors(palette)
        b = direct_colors(palette, rgb565=True)
        self.assertEqual(a[0], 0x7fff)
        self.assertEqual(b[0], 0xffff)
        # Последняя ступень: 63 / 32 = 1 → красный/синий уже 0, зелёный RGB565 ещё 1.
        self.assertEqual(a[-1], 0)
        self.assertEqual(b[-1], 0x20)
        with self.assertRaises(ValueError):
            direct_colors(bytes([64]) * 768)


if __name__ == "__main__":
    unittest.main()
