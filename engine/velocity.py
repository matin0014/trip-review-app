"""Speed computation.

Responsibility: per-segment speeds from a route, the glitch-filtered median
moving speed (`v_A` / `v_B` / per-trip), and the displacement/duration fallback
for sparse or empty routes. Pure; depends on `geo`, `models`, `params`.

TODO (Step 1c): implement segment speeds + `median_moving_speed(...)`.
"""

from __future__ import annotations
