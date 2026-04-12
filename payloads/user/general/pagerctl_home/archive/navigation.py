"""Spatial navigation — find nearest selectable element in a direction."""

import math


def find_nearest(elements, current_idx, direction):
    """Find the nearest selectable element in the given direction.

    Args:
        elements: list of Element objects
        current_idx: index of currently selected element
        direction: 'up', 'down', 'left', 'right'

    Returns:
        index of nearest element, or current_idx if none found
    """
    selectables = [(i, e) for i, e in enumerate(elements) if e.selectable and i != current_idx]
    if not selectables:
        return current_idx

    current = elements[current_idx]
    cx, cy = current.center_x(), current.center_y()

    best_idx = current_idx
    best_score = float('inf')

    for idx, elem in selectables:
        ex, ey = elem.center_x(), elem.center_y()
        dx = ex - cx
        dy = ey - cy

        # Check if element is in the right direction
        in_direction = False
        if direction == 'up' and dy < -5:
            in_direction = True
        elif direction == 'down' and dy > 5:
            in_direction = True
        elif direction == 'left' and dx < -5:
            in_direction = True
        elif direction == 'right' and dx > 5:
            in_direction = True

        if not in_direction:
            continue

        # Score: distance with bias toward the primary axis
        dist = math.sqrt(dx * dx + dy * dy)

        # Penalize off-axis distance
        if direction in ('up', 'down'):
            score = dist + abs(dx) * 2  # Prefer vertically aligned
        else:
            score = dist + abs(dy) * 2  # Prefer horizontally aligned

        if score < best_score:
            best_score = score
            best_idx = idx

    # If nothing found in direction, wrap around
    if best_idx == current_idx:
        best_idx = _wrap_around(elements, current_idx, direction)

    return best_idx


def _wrap_around(elements, current_idx, direction):
    """Wrap to the opposite side when no element found in direction."""
    selectables = [(i, e) for i, e in enumerate(elements) if e.selectable and i != current_idx]
    if not selectables:
        return current_idx

    if direction == 'up':
        # Go to bottom-most
        return max(selectables, key=lambda x: x[1].center_y())[0]
    elif direction == 'down':
        # Go to top-most
        return min(selectables, key=lambda x: x[1].center_y())[0]
    elif direction == 'left':
        # Go to right-most
        return max(selectables, key=lambda x: x[1].center_x())[0]
    elif direction == 'right':
        # Go to left-most
        return min(selectables, key=lambda x: x[1].center_x())[0]

    return current_idx
