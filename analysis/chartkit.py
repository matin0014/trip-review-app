"""Shared chart styling and the circular smoother the start-time curves use.

Two things live here so the per-wave charts and the wave-vs-wave comparisons
cannot drift apart: the visual tokens, and `circular_kde`.

Why a smoother at all: binning start times by hour makes the curve jag between
adjacent hours, and those jags are an artefact of the bin edges, not of
behaviour -- a trip at 08:29 and one at 08:31 land in different bars. The raw
times carry minutes, so the density can be estimated from them directly and the
bins skipped entirely.

Why CIRCULAR: hour-of-day wraps. A plain kernel would taper to zero at 00:00 and
23:59 as though the night were the edge of the data, inventing a trough at
midnight and losing the late-night trips that belong beside the early ones.
"""

from __future__ import annotations

import numpy as np

# --- ink -------------------------------------------------------------------
# White surface. Every text, grid and spine colour is set explicitly against it:
# matplotlib's own defaults assume a white ground too, but leaving any of these
# implicit means a future dark-mode pass silently breaks them, so they are all
# named here rather than left to the default.
SURFACE = "#ffffff"
INK, MUTED, FAINT, GRID = "#12151b", "#5b6478", "#c3c8d3", "#e5e7eb"

# Red / green: one wave, one colour, everywhere that wave appears (a per-wave
# chart and the wave-vs-wave comparison never disagree on which colour is which
# wave). Red against green is exactly the pairing red-green colour blindness
# collapses, so every comparison chart carries a legend AND labels each series
# directly: identity here is never carried by colour alone.
W2, W3 = "#dc2626", "#16a34a"
# A highlight colour for "this bar is the outlier", used WITHIN a single-wave
# chart (e.g. the >7-day tail in `days_per_participant`). Deliberately not one
# of W2/W3: reusing another wave's colour there would read as a cross-wave
# comparison that isn't actually being made.
AMBER = "#d97706"
SERIES, ACCENT, MISSING = "#16a34a", "#d97706", "#8b93a7"


def figure(*args, **kwargs):
    """`plt.subplots` with the dark ground already painted onto figure and axes."""
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(*args, **kwargs)
    fig.patch.set_facecolor(SURFACE)
    for a in (ax.flat if hasattr(ax, "flat") else [ax]):
        a.set_facecolor(SURFACE)
    return fig, ax


def save(fig, out, dpi=170):
    """`savefig` defaults to a white ground regardless of the figure's own, which
    would frame every dark chart in a bright border. Always go through here."""
    fig.savefig(out, dpi=dpi, facecolor=SURFACE)


def style(ax, title=None, xlabel=None, ylabel=None, axis="y"):
    """Recessive frame: the data should be the only thing with weight."""
    ax.set_facecolor(SURFACE)
    if title:
        ax.set_title(title, fontsize=15, color=INK, pad=16, loc="left", weight="600")
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=10.5, color=MUTED, labelpad=8)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=10.5, color=MUTED, labelpad=8)
    ax.grid(axis=axis, color=GRID, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=10, length=0)


def headline(fig, text, y=0.03, x=0.07):
    fig.text(x, y, text, fontsize=13, color=INK, va="bottom", weight="bold")


def hour_of_day(series) -> np.ndarray:
    """Start times as fractional hours (08:30 -> 8.5), minutes kept.

    `series` holds the workbook's time strings; anything unparseable is dropped
    rather than coerced to midnight, which would pile phantom trips onto hour 0.
    """
    import re

    out = []
    for v in series.dropna():
        m = re.match(r"^(\d{1,2}):(\d{2})", str(v))
        if m:
            out.append(int(m.group(1)) + int(m.group(2)) / 60.0)
    return np.asarray(out, dtype=float)


def circular_kde(hours: np.ndarray, bw: float = 0.7, n: int = 721) -> tuple:
    """``(grid, density)`` over 0..24 with the day treated as a circle.

    Each observation contributes a Gaussian bump; summing them gives a curve
    with no bin edges to jag on. Wrapping is done by measuring the distance
    between an observation and a grid point the short way round the clock, so
    23:50 and 00:10 are 20 minutes apart rather than 23 hours.

    `bw` is in hours: ~0.7 keeps the morning and evening peaks distinct while
    removing the minute-to-minute noise. Density is per hour, so it integrates
    to 1 and two waves of different size can be laid over each other.
    """
    grid = np.linspace(0, 24, n)
    if hours.size == 0:
        return grid, np.zeros_like(grid)
    d = np.abs(grid[:, None] - hours[None, :])
    d = np.minimum(d, 24 - d)                      # the short way round the clock
    dens = np.exp(-0.5 * (d / bw) ** 2).sum(axis=1)
    dens /= bw * np.sqrt(2 * np.pi) * hours.size   # -> density per hour
    return grid, dens


def hour_axis(ax, step: int = 3):
    ax.set_xticks(range(0, 25, step))
    ax.set_xticklabels([f"{h:02d}" if h < 24 else "24" for h in range(0, 25, step)])
    ax.set_xlim(0, 24)


def peak_label(ax, grid, dens, color, text=None, dy=10):
    """Mark the busiest moment; a distribution's headline is usually its peak."""
    i = int(np.argmax(dens))
    h = grid[i]
    ax.annotate(text or f"peak {int(h):02d}:{int(round((h % 1) * 60)):02d}",
                xy=(h, dens[i]), xytext=(0, dy), textcoords="offset points",
                ha="center", fontsize=9.5, color=color, weight="600")
