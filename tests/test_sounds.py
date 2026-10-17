"""RIFF: синтетические границы, неизвестные чанки и последовательные Sound."""

import struct
import unittest

from smuta_build.dat import parse
from smuta_build.sounds import parse_wave, sound_bindings
from test_text import fixture as text_fixture


def chunk(tag, payload, pad=b"\x7f"):
    return tag + struct.pack("<I", len(payload)) + payload + (pad if len(payload) & 1 else b"")


def wave(*records):
    body = b"WAVE" + b"".join(records)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def fmt(channels=1, rate=11025, bits=8, extension=b""):
    align = channels * (bits // 8)
    return chunk(b"fmt ", struct.pack("<HHIIHH", 1, channels, rate, rate*align, align, bits) + extension)


def archive(items):
    directory, payload = bytearray(struct.pack("<H", len(items))), bytearray()
    for name, raw in items:
        directory.extend((name.encode("cp1251") + b"\0").ljust(16, b"\0") +
                         struct.pack("<II", len(raw), 2 + 30*len(items) + len(payload)) + b"\0"*6)
        payload.extend(raw)
    return parse(bytes(directory + payload))


class WaveTests(unittest.TestCase):
    def test_unknown_chunks_padding_info_and_fmt_extension_preserved(self):
        raw = wave(chunk(b"JUNK", b"fmt data!"), fmt(extension=b"\0\0"),
                   chunk(b"data", b"\0\x80\xff"),
                   chunk(b"LIST", b"INFO" + chunk(b"ISFT", b"abc")))
        parsed = parse_wave(raw)
        self.assertEqual(parsed["sample_frames"], 3)
        self.assertEqual(parsed["duration"], {"numerator": 3, "denominator": 11025, "unit": "seconds"})
        self.assertEqual(parsed["chunks"][0]["padding_hex"], "7f")
        self.assertEqual(parsed["chunks"][-1]["children"][0]["length"], 3)
        self.assertEqual(parsed["fmt_hex"][-4:], "0000")

    def test_zero_data_and_fact_are_not_silently_repaired(self):
        p = parse_wave(wave(fmt(), chunk(b"fact", struct.pack("<I", 9)), chunk(b"data", b"")))
        self.assertEqual(p["sample_frames"], 0)
        self.assertEqual(p["fact_sample_frames_raw"], 9)
        self.assertFalse(p["fact_matches_pcm"])

    def test_pcm_frame_size_and_duration(self):
        p = parse_wave(wave(fmt(2, 22050, 16), chunk(b"data", bytes(12))))
        self.assertEqual((p["block_align"], p["sample_frames"]), (4, 3))
        with self.assertRaises(ValueError):
            parse_wave(wave(fmt(2, 22050, 16), chunk(b"data", bytes(3))))

    def test_truncation_and_riff_length(self):
        raw = wave(fmt(), chunk(b"data", b"abc"))
        for n in range(len(raw)):
            with self.subTest(n=n), self.assertRaises(ValueError):
                parse_wave(raw[:n])
        for bad in (raw + b"x", b"RIFX" + raw[4:], raw[:8] + b"AVI " + raw[12:]):
            with self.assertRaises(ValueError):
                parse_wave(bad)

    def test_chunk_boundaries_missing_padding_and_nested_overflow(self):
        for record in (b"data" + struct.pack("<I", 0xffffffff), b"x",
                       b"data\1\0\0\0x", chunk(b"LIST", b"INF"),
                       chunk(b"LIST", b"INFO" + b"X"*7)):
            with self.subTest(record=record), self.assertRaises(ValueError):
                parse_wave(wave(fmt(), record))

    def test_duplicate_missing_and_out_of_order_required_chunks(self):
        f, d = fmt(), chunk(b"data", b"x")
        for records in ((f,), (d,), (f, f, d), (f, d, d), (d, f)):
            with self.assertRaises(ValueError):
                parse_wave(wave(*records))

    def test_invalid_fmt_codec_rates_and_extension(self):
        good = struct.pack("<HHIIHH", 1, 1, 11025, 11025, 1, 8)
        cases = [good[:15], good+b"x", good+b"\1\0", good+b"\0\0x"]
        for offset, value, code in ((0, 3, "H"), (2, 0, "H"), (4, 0, "I"),
                                    (8, 123, "I"), (12, 2, "H"), (14, 7, "H")):
            raw = bytearray(good)
            struct.pack_into("<"+code, raw, offset, value)
            cases.append(bytes(raw))
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_wave(wave(chunk(b"fmt ", raw), chunk(b"data", b"xx")))

    def test_invalid_fact(self):
        for extra in ((chunk(b"fact", b"abc"),), (chunk(b"fact", bytes(4)),)*2):
            with self.assertRaises(ValueError):
                parse_wave(wave(fmt(), *extra, chunk(b"data", b"x")))

    def test_context_inheritance_and_explicit_monster_sounds(self):
        objects = (b"Sound = ONE\nA 4 0 1 0 1\nB 4 0 1 0 1\nSound = TWO\n"
                   b"C 9 0 1 0 1 1 2 3 ANIM ANIM TWO ONE 10")
        a = archive([("LEVEL1-1", text_fixture(objects=objects)), ("ONE", b""),
                     ("ONE", b""), ("TWO", b""), ("ANIM", b"")])
        bindings = sound_bindings(a, {1, 2, 3})
        self.assertEqual([b["target_index"] for b in bindings], [1, 3, 3, 1])
        self.assertEqual([u["key_byte"] for u in bindings[0]["uses"]], [65, 66])
        self.assertEqual([b["uses"][0]["binding"] for b in bindings[2:]], ["sound0", "sound1"])
        self.assertTrue(all(b["event"] is None for b in bindings))

    def test_unresolved_or_nonwave_reference_fails(self):
        for names, indices in (([], set()), ([("ONE", b"")], set())):
            a = archive([("LEVEL1-1", text_fixture(objects=b"Sound = ONE\nA 4 0 1 0 1")), *names])
            with self.assertRaises(ValueError):
                sound_bindings(a, indices)
