#!/usr/bin/env python3

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ICONSET_DIR = ROOT / "assets" / "InterviewTranscriber.iconset"
ICNS_PATH = ROOT / "assets" / "InterviewTranscriber.icns"


ICON_SIZES = [16, 32, 128, 256, 512]


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def smoothstep(edge0: float, edge1: float, x: float) -> float:
    if edge0 == edge1:
        return 0.0
    t = clamp((x - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(round(a[i] * (1.0 - t) + b[i] * t)) for i in range(3))


def png_bytes(width: int, height: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        start = y * stride
        raw.extend(rgba[start : start + stride])

    png = bytearray()
    png.extend(b"\x89PNG\r\n\x1a\n")
    png.extend(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)))
    png.extend(chunk(b"IDAT", zlib.compress(bytes(raw), level=9)))
    png.extend(chunk(b"IEND", b""))
    return bytes(png)


def write_png(path: Path, width: int, height: int, rgba: bytes) -> bytes:
    png = png_bytes(width, height, rgba)
    path.write_bytes(png)
    return png


def write_icns(path: Path, chunks: list[tuple[str, bytes]]) -> None:
    payload = bytearray()
    for icon_type, data in chunks:
        payload.extend(icon_type.encode("ascii"))
        payload.extend(struct.pack(">I", len(data) + 8))
        payload.extend(data)

    icns = bytearray()
    icns.extend(b"icns")
    icns.extend(struct.pack(">I", len(payload) + 8))
    icns.extend(payload)
    path.write_bytes(icns)


def signed_round_rect(px: float, py: float, x: float, y: float, w: float, h: float, r: float) -> float:
    cx = x + w / 2.0
    cy = y + h / 2.0
    qx = abs(px - cx) - w / 2.0 + r
    qy = abs(py - cy) - h / 2.0 + r
    ox = max(qx, 0.0)
    oy = max(qy, 0.0)
    return math.hypot(ox, oy) + min(max(qx, qy), 0.0) - r


def coverage_from_distance(distance: float, aa: float) -> float:
    return clamp(0.5 - distance / aa)


def composite(base: tuple[float, float, float, float], top_rgb: tuple[int, int, int], alpha: float) -> tuple[float, float, float, float]:
    alpha = clamp(alpha)
    br, bg, bb, ba = base
    tr, tg, tb = [c / 255.0 for c in top_rgb]
    out_a = alpha + ba * (1.0 - alpha)
    if out_a <= 0.0:
        return 0.0, 0.0, 0.0, 0.0
    out_r = (tr * alpha + br * ba * (1.0 - alpha)) / out_a
    out_g = (tg * alpha + bg * ba * (1.0 - alpha)) / out_a
    out_b = (tb * alpha + bb * ba * (1.0 - alpha)) / out_a
    return out_r, out_g, out_b, out_a


def generate_icon(size: int) -> bytes:
    px = bytearray(size * size * 4)
    aa = max(1.0, size / 180.0)

    bg_top = (40, 43, 49)
    bg_bottom = (17, 19, 24)
    outline = (78, 84, 94)
    panel_top = (52, 56, 64)
    panel_bottom = (29, 32, 39)
    panel_outline = (102, 109, 120)
    mic_top = (255, 179, 105)
    mic_bottom = (214, 109, 66)
    text_line = (232, 235, 240)
    text_dim = (164, 170, 180)
    accent = (248, 129, 76)

    outer_margin = size * 0.02
    outer_radius = size * 0.225
    outer_x = outer_margin
    outer_y = outer_margin
    outer_w = size - outer_margin * 2.0
    outer_h = size - outer_margin * 2.0

    panel_w = size * 0.68
    panel_h = size * 0.54
    panel_x = size * 0.17
    panel_y = size * 0.22
    panel_radius = size * 0.11

    mic_w = size * 0.14
    mic_h = size * 0.28
    mic_x = panel_x + size * 0.08
    mic_y = panel_y + size * 0.08
    mic_radius = mic_w / 2.0

    stem_w = mic_w * 0.28
    stem_h = size * 0.08
    stem_x = mic_x + mic_w / 2.0 - stem_w / 2.0
    stem_y = mic_y + mic_h - size * 0.01

    base_w = mic_w * 0.82
    base_h = size * 0.022
    base_x = mic_x + mic_w / 2.0 - base_w / 2.0
    base_y = stem_y + stem_h + size * 0.018

    line_specs = [
        (panel_x + size * 0.30, panel_y + size * 0.12, size * 0.28, size * 0.032, text_line),
        (panel_x + size * 0.30, panel_y + size * 0.22, size * 0.22, size * 0.032, text_dim),
        (panel_x + size * 0.30, panel_y + size * 0.32, size * 0.30, size * 0.032, text_line),
    ]

    wave_y = panel_y + panel_h + size * 0.10
    wave_left = panel_x + size * 0.09
    wave_right = panel_x + panel_w - size * 0.09
    wave_width = wave_right - wave_left
    wave_amp = size * 0.028
    wave_thickness = size * 0.020

    dot_x = panel_x + panel_w - size * 0.06
    dot_y = panel_y + size * 0.07
    dot_r = size * 0.026

    idx = 0
    for y in range(size):
        fy = y + 0.5
        for x in range(size):
            fx = x + 0.5
            color = (0.0, 0.0, 0.0, 0.0)

            outer_dist = signed_round_rect(fx, fy, outer_x, outer_y, outer_w, outer_h, outer_radius)
            outer_fill = coverage_from_distance(outer_dist, aa)
            if outer_fill > 0.0:
                t = clamp((fy - outer_y) / outer_h)
                base_color = mix(bg_top, bg_bottom, t)
                vignette = clamp(1.0 - (abs(fx - size / 2.0) / (size / 2.0)) * 0.10)
                base_color = tuple(int(channel * vignette) for channel in base_color)
                color = composite(color, base_color, outer_fill)

                border_alpha = outer_fill * (1.0 - coverage_from_distance(outer_dist + size * 0.012, aa))
                if border_alpha > 0.0:
                    color = composite(color, outline, border_alpha * 0.95)

            panel_dist = signed_round_rect(fx, fy, panel_x, panel_y, panel_w, panel_h, panel_radius)
            panel_fill = coverage_from_distance(panel_dist, aa)
            if panel_fill > 0.0:
                t = clamp((fy - panel_y) / panel_h)
                panel_color = mix(panel_top, panel_bottom, t)
                color = composite(color, panel_color, panel_fill * 0.98)
                panel_border = panel_fill * (1.0 - coverage_from_distance(panel_dist + size * 0.008, aa))
                if panel_border > 0.0:
                    color = composite(color, panel_outline, panel_border * 0.85)

            mic_dist = signed_round_rect(fx, fy, mic_x, mic_y, mic_w, mic_h, mic_radius)
            mic_fill = coverage_from_distance(mic_dist, aa)
            if mic_fill > 0.0:
                t = clamp((fy - mic_y) / mic_h)
                mic_color = mix(mic_top, mic_bottom, t)
                color = composite(color, mic_color, mic_fill)

            stem_dist = signed_round_rect(fx, fy, stem_x, stem_y, stem_w, stem_h, stem_w / 2.0)
            stem_fill = coverage_from_distance(stem_dist, aa)
            if stem_fill > 0.0:
                color = composite(color, mic_bottom, stem_fill * 0.95)

            base_dist = signed_round_rect(fx, fy, base_x, base_y, base_w, base_h, base_h / 2.0)
            base_fill = coverage_from_distance(base_dist, aa)
            if base_fill > 0.0:
                color = composite(color, mic_bottom, base_fill * 0.92)

            for line_x, line_y, line_w, line_h, line_color in line_specs:
                line_dist = signed_round_rect(fx, fy, line_x, line_y, line_w, line_h, line_h / 2.0)
                line_fill = coverage_from_distance(line_dist, aa)
                if line_fill > 0.0:
                    color = composite(color, line_color, line_fill * 0.98)

            if wave_left <= fx <= wave_right:
                norm_x = (fx - wave_left) / max(wave_width, 1.0)
                centerline = wave_y + math.sin(norm_x * math.pi * 2.4) * wave_amp * 0.70
                centerline += math.sin(norm_x * math.pi * 8.0) * wave_amp * 0.28
                distance = abs(fy - centerline) - wave_thickness / 2.0
                wave_fill = coverage_from_distance(distance, aa)
                if wave_fill > 0.0:
                    wave_color = accent if norm_x < 0.42 else text_line
                    color = composite(color, wave_color, wave_fill * 0.96)

            dot_dist = math.hypot(fx - dot_x, fy - dot_y) - dot_r
            dot_fill = coverage_from_distance(dot_dist, aa)
            if dot_fill > 0.0:
                color = composite(color, accent, dot_fill)

            px[idx] = int(round(clamp(color[0]) * 255.0))
            px[idx + 1] = int(round(clamp(color[1]) * 255.0))
            px[idx + 2] = int(round(clamp(color[2]) * 255.0))
            px[idx + 3] = int(round(clamp(color[3]) * 255.0))
            idx += 4

    return bytes(px)


def main() -> None:
    ICONSET_DIR.mkdir(parents=True, exist_ok=True)
    for existing in ICONSET_DIR.glob("*.png"):
        existing.unlink()

    png_map: dict[int, bytes] = {}
    for size in [16, 32, 64, 128, 256, 512, 1024]:
        png_map[size] = png_bytes(size, size, generate_icon(size))

    for size in ICON_SIZES:
        (ICONSET_DIR / f"icon_{size}x{size}.png").write_bytes(png_map[size])
        retina = size * 2
        (ICONSET_DIR / f"icon_{size}x{size}@2x.png").write_bytes(png_map[retina])

    write_icns(
        ICNS_PATH,
        [
            ("icp4", png_map[16]),
            ("icp5", png_map[32]),
            ("icp6", png_map[64]),
            ("ic07", png_map[128]),
            ("ic08", png_map[256]),
            ("ic09", png_map[512]),
            ("ic10", png_map[1024]),
        ],
    )


if __name__ == "__main__":
    main()
