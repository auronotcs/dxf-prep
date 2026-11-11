"""Color conversion and opacity helpers used by the desktop appearance UI."""

from __future__ import annotations


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def rgb_to_hex(red: float, green: float, blue: float) -> str:
    channels = [round(clamp(float(value), 0, 255)) for value in (red, green, blue)]
    return "#" + "".join(f"{value:02x}" for value in channels)


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = str(color).strip().lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    if len(value) != 6:
        raise ValueError("Color must be a three- or six-digit hexadecimal value.")
    try:
        return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError as exc:
        raise ValueError("Color contains invalid hexadecimal digits.") from exc


def cmyk_to_rgb(cyan: float, magenta: float, yellow: float, black: float) -> tuple[int, int, int]:
    c, m, y, k = [clamp(float(value) / 100.0, 0, 1) for value in (cyan, magenta, yellow, black)]
    return (
        round(255 * (1 - c) * (1 - k)),
        round(255 * (1 - m) * (1 - k)),
        round(255 * (1 - y) * (1 - k)),
    )


def rgb_to_cmyk(red: float, green: float, blue: float) -> tuple[float, float, float, float]:
    r, g, b = [clamp(float(value) / 255.0, 0, 1) for value in (red, green, blue)]
    black = 1 - max(r, g, b)
    if black >= 1 - 1e-12:
        return 0.0, 0.0, 0.0, 100.0
    denominator = 1 - black
    return tuple(round(value * 100, 3) for value in (
        (1 - r - black) / denominator,
        (1 - g - black) / denominator,
        (1 - b - black) / denominator,
        black,
    ))


def blend_opacity(color: str, opacity: float, background: str = "#ffffff") -> str:
    """Approximate line opacity for Tk Canvas by blending against its background."""
    foreground = hex_to_rgb(color)
    backdrop = hex_to_rgb(background)
    alpha = clamp(float(opacity), 0, 1)
    return rgb_to_hex(*(foreground[index] * alpha + backdrop[index] * (1 - alpha) for index in range(3)))
