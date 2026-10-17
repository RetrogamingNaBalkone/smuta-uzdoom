"""FL: независимые миниатюры, повреждённые диапазоны и угловые границы."""

import struct
import unittest

from smuta_build.animations import callback_updates, decode_fl, delay_counter, frame_index


def fixture(step=0, count=2):
    total = (360 // step if step else 1) * count
    table, frames = [], []
    for i in range(total):
        table.append(struct.pack("<hhhI", 100 + i, -i, i, 0x12345678 + i))
        frames.append(struct.pack("<HHhhhII", 3, 2, 9, 7, -3, 6, 0x11223344)
                      + bytes((i % 256, 0, 255, 7, 8, 9)))
    return struct.pack("<hhhhI", step, count, total, -7, 0x76543210) + b"".join(table + frames)


class AnimationTests(unittest.TestCase):
    def test_scheduler_zero_and_integer_boundaries(self):
        expected = {0: (0, 1), 1: (0, 1), 41: (0, 1), 42: (1, 2),
                    83: (1, 2), 84: (2, 3), 100: (2, 3), 200: (4, 5), 1000: (24, 25)}
        for value, (counter, updates) in expected.items():
            self.assertEqual(delay_counter(value), counter)
            self.assertEqual(callback_updates(counter), updates)
        for value in (-1, 32768):
            with self.assertRaises(ValueError):
                delay_counter(value)

    def test_weapon_offsets_preserve_sign_and_are_separate_from_anchor(self):
        a = decode_fl(fixture())
        self.assertEqual(a.metadata["frames"][1]["weapon_screen_offset"],
                         {"x": -1, "y": 1, "unit": "source-screen-pixels"})
        self.assertIsNone(a.frames[1].metadata["anchor"])

    def test_retains_unknown_words_pointers_headers_and_indices(self):
        raw = fixture()
        a = decode_fl(raw)
        self.assertEqual((a.angle_step, a.frames_per_direction, a.directions), (0, 2, 1))
        self.assertEqual(a.metadata["header_hex"], raw[:12].hex())
        self.assertEqual(a.metadata["unknown_word6"], -7)
        self.assertEqual(a.metadata["runtime_table_pointer_raw"], 0x76543210)
        for i, (image, record) in enumerate(zip(a.frames, a.metadata["frames"])):
            self.assertEqual(image.pixels, bytes((i, 0, 255, 7, 8, 9)))
            self.assertEqual(record["table_hex"], raw[12 + 10*i:22 + 10*i].hex())
            self.assertEqual(record["spr_offset"], 32 + 24*i)
            self.assertEqual(record["word2_raw"], -i)
            self.assertIsNone(image.metadata["anchor"])
            self.assertIsNone(image.metadata["transparent_index"])
        self.assertIsNone(a.metadata["duration_seconds"])

    def test_direction_major_order_and_rounding(self):
        a = decode_fl(fixture(45, 2))
        self.assertEqual(len(a.frames), 16)
        expected = {0: 0, 22: 0, 23: 1, 67: 1, 68: 2, 337: 7, 338: 0, 359: 0}
        for angle, direction in expected.items():
            self.assertEqual(frame_index(a, 1, angle), 2*direction + 1)
        for angle in range(360):
            self.assertLess(frame_index(a, 1, angle), 16)
        self.assertEqual(frame_index(a, -1, 45), 2)
        self.assertEqual(frame_index(a, 999, 45), 3)
        for angle in (-1, 360):
            with self.assertRaises(ValueError):
                frame_index(a, 0, angle)

    def test_non_directional_animation_ignores_valid_angle(self):
        a = decode_fl(fixture())
        self.assertEqual([frame_index(a, 1, angle) for angle in range(360)], [1] * 360)

    def test_forty_directions_and_odd_step_rounding(self):
        a = decode_fl(fixture(9, 1))
        self.assertEqual((a.directions, len(a.frames)), (40, 40))
        for angle, expected in ((0, 0), (4, 0), (5, 1), (13, 1), (14, 2),
                                (355, 39), (356, 0), (359, 0)):
            self.assertEqual(frame_index(a, 0, angle), expected)

    def test_all_truncations_and_trailing_data_rejected(self):
        raw = fixture()
        for size in range(len(raw)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                decode_fl(raw[:size])
        with self.assertRaises(ValueError):
            decode_fl(raw + b"\0")

    def test_invalid_counts_and_angle_step_rejected(self):
        raw = fixture()
        for offset, value in ((0, -45), (0, 47), (0, 361), (0, 45),
                              (2, 0), (2, -1), (2, 3), (4, 0), (4, -1), (4, 3)):
            broken = bytearray(raw)
            struct.pack_into("<h", broken, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                decode_fl(bytes(broken))

    def test_embedded_spr_size_is_not_guessed_from_dimensions(self):
        raw = fixture()
        for offset, fmt, value in ((32, "H", 0), (34, "H", 65535),
                                    (42, "I", 0), (42, "I", 5), (42, "I", 7),
                                    (42, "I", 0xffffffff)):
            broken = bytearray(raw)
            struct.pack_into("<" + fmt, broken, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                decode_fl(bytes(broken))


if __name__ == "__main__":
    unittest.main()
