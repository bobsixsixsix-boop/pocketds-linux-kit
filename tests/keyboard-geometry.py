#!/usr/bin/env python3
"""Pure fixtures for the lower-screen keyboard geometry resolver."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components" / "keyboard"))

from screen_geometry import (  # noqa: E402
    FALLBACK_LOWER_SCREEN,
    MAX_CACHE_BYTES,
    ScreenGeometry,
    geometry_from_payload,
    load_lower_screen_geometry,
)


NOW_MS = 2_000_000


def payload(**overrides: object) -> dict[str, object]:
    sample: dict[str, object] = {
        "display_status": "ok",
        "display_sample_unix_ms": NOW_MS - 1_000,
        "display_dsi2_enabled": True,
        "display_dsi2_logical_x": 283,
        "display_dsi2_logical_y": 720,
        "display_dsi2_logical_width": 819,
        "display_dsi2_logical_height": 614,
    }
    sample.update(overrides)
    return sample


class GeometryPayloadTests(unittest.TestCase):
    def test_fresh_complete_dsi2_geometry_is_authoritative(self) -> None:
        self.assertEqual(
            geometry_from_payload(payload(), now_ms=NOW_MS),
            ScreenGeometry(283, 720, 819, 614),
        )

    def test_stale_partial_disabled_fractional_and_absurd_values_fail(self) -> None:
        invalid = (
            payload(display_status="partial"),
            payload(display_dsi2_enabled=False),
            payload(display_sample_unix_ms=NOW_MS - 120_001),
            payload(display_sample_unix_ms=NOW_MS + 5_001),
            payload(display_dsi2_logical_width=819.5),
            payload(display_dsi2_logical_height=100),
            payload(display_dsi2_logical_x=20_000),
        )
        for sample in invalid:
            with self.subTest(sample=sample), self.assertRaises(ValueError):
                geometry_from_payload(sample, now_ms=NOW_MS)


class GeometryFileTests(unittest.TestCase):
    def test_fallback_is_replaced_when_live_geometry_arrives(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pocketds-keyboard-geometry-") as temporary:
            runtime = Path(temporary)
            self.assertEqual(
                load_lower_screen_geometry(runtime_dir=runtime, now_ms=NOW_MS),
                FALLBACK_LOWER_SCREEN,
            )
            cache = runtime / "pocketds-gpu-status.json"
            cache.write_text(
                json.dumps(
                    payload(
                        display_dsi2_logical_x=401,
                        display_dsi2_logical_y=810,
                        display_dsi2_logical_width=768,
                        display_dsi2_logical_height=576,
                    )
                ),
                encoding="utf-8",
            )
            cache.chmod(0o600)
            self.assertEqual(
                load_lower_screen_geometry(runtime_dir=runtime, now_ms=NOW_MS),
                ScreenGeometry(401, 810, 768, 576),
            )

    def test_reader_uses_fresh_private_runtime_sample(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pocketds-keyboard-geometry-") as temporary:
            runtime = Path(temporary)
            (runtime / "pocketds-gpu-status.json").write_text(
                json.dumps(payload(display_dsi2_logical_width=913)),
                encoding="utf-8",
            )
            (runtime / "pocketds-gpu-status.json").chmod(0o600)
            self.assertEqual(
                load_lower_screen_geometry(runtime_dir=runtime, now_ms=NOW_MS),
                ScreenGeometry(283, 720, 913, 614),
            )

    def test_missing_malformed_or_stale_sample_uses_verified_fallback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pocketds-keyboard-geometry-") as temporary:
            runtime = Path(temporary)
            self.assertEqual(
                load_lower_screen_geometry(runtime_dir=runtime, now_ms=NOW_MS),
                FALLBACK_LOWER_SCREEN,
            )
            (runtime / "pocketds-gpu-status.json").write_text("{", encoding="utf-8")
            (runtime / "pocketds-gpu-status.json").chmod(0o600)
            self.assertEqual(
                load_lower_screen_geometry(runtime_dir=runtime, now_ms=NOW_MS),
                FALLBACK_LOWER_SCREEN,
            )
            (runtime / "pocketds-gpu-status.json").write_text(
                json.dumps(payload(display_sample_unix_ms=1)),
                encoding="utf-8",
            )
            (runtime / "pocketds-gpu-status.json").chmod(0o600)
            self.assertEqual(
                load_lower_screen_geometry(runtime_dir=runtime, now_ms=NOW_MS),
                FALLBACK_LOWER_SCREEN,
            )

    def test_public_linked_non_utf8_and_oversized_cache_fail_safe(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pocketds-keyboard-geometry-") as temporary:
            runtime = Path(temporary)
            cache = runtime / "pocketds-gpu-status.json"
            cache.write_text(json.dumps(payload()), encoding="utf-8")
            cache.chmod(0o644)
            self.assertEqual(
                load_lower_screen_geometry(runtime_dir=runtime, now_ms=NOW_MS),
                FALLBACK_LOWER_SCREEN,
            )

            cache.chmod(0o600)
            linked = runtime / "linked.json"
            os.link(cache, linked)
            self.assertEqual(
                load_lower_screen_geometry(runtime_dir=runtime, now_ms=NOW_MS),
                FALLBACK_LOWER_SCREEN,
            )
            linked.unlink()
            cache.unlink()
            target = runtime / "target.json"
            target.write_text(json.dumps(payload()), encoding="utf-8")
            target.chmod(0o600)
            cache.symlink_to(target)
            self.assertEqual(
                load_lower_screen_geometry(runtime_dir=runtime, now_ms=NOW_MS),
                FALLBACK_LOWER_SCREEN,
            )

            cache.unlink()
            cache.write_bytes(b"\xff")
            cache.chmod(0o600)
            self.assertEqual(
                load_lower_screen_geometry(runtime_dir=runtime, now_ms=NOW_MS),
                FALLBACK_LOWER_SCREEN,
            )
            cache.unlink()
            with cache.open("wb") as stream:
                stream.truncate(MAX_CACHE_BYTES + 1)
            cache.chmod(0o600)
            self.assertEqual(
                load_lower_screen_geometry(runtime_dir=runtime, now_ms=NOW_MS),
                FALLBACK_LOWER_SCREEN,
            )


if __name__ == "__main__":
    unittest.main()
