"""Screen-space edge scrolling for a held geometry drag (no model mutation)."""
import math


def edge_pan_delta(x, y, width, height, elapsed=1 / 30):
    """Canvas-origin delta: smooth 36px edge band, capped speed and time step.

    Outside pointers saturate rather than accelerate without bound. Corners
    have the same maximum vector speed as edges; speed is independent of zoom.
    """
    if width <= 1 or height <= 1:
        return 0.0, 0.0
    def axis(position, extent):
        band = min(36.0, extent / 4)
        if position < band:
            return min(1.0, (band - position) / band) ** 2
        if position > extent - band:
            return -min(1.0, (position - extent + band) / band) ** 2
        return 0.0
    dx, dy = axis(x, width), axis(y, height)
    magnitude = max(1.0, math.hypot(dx, dy))
    distance = 600 * max(0.0, min(float(elapsed), .05))
    return dx * distance / magnitude, dy * distance / magnitude
