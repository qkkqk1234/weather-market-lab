"""Small plotting helpers so every figure in the README is regenerable.

One visual convention throughout: the market is grey, the model is coloured,
and the perfect-calibration reference is a dashed diagonal. Anything that beats
grey is a result; anything that does not is the finding.
"""

from __future__ import annotations

import os
from datetime import date

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

REPORTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

MARKET = "#8a8f98"
MODEL = "#2f6fed"
WARN = "#d1495b"
GRID = "#e4e6eb"


def _style(ax, title, xlabel, ylabel):
    ax.set_title(title, fontsize=11, loc="left", pad=10)
    ax.set_xlabel(xlabel, fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(labelsize=8)


def save(fig, name):
    os.makedirs(REPORTS, exist_ok=True)
    path = os.path.join(REPORTS, name)
    fig.savefig(path, dpi=144, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def calibration_plot(points, name="market_calibration.png"):
    """points: [(mean price in bin, realised frequency, n)]"""
    fig, ax = plt.subplots(figsize=(5.2, 4.4))
    ax.plot([0, 1], [0, 1], "--", color="#b9bec7", linewidth=1, label="perfect calibration")
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    sizes = [max(18, min(320, p[2] / 6)) for p in points]
    ax.scatter(xs, ys, s=sizes, color=MARKET, edgecolor="white", linewidth=1.2, zorder=3,
               label="market price vs outcome")
    _style(ax, "Market price is what it says it is", "quoted price", "realised win rate")
    handles, labels = ax.get_legend_handles_labels()
    legend = ax.legend(handles, labels, fontsize=8, frameon=False,
                       loc="upper left", scatterpoints=1)
    for handle in legend.legend_handles[1:]:
        handle.set_sizes([28])  # the marker area encodes n; don't let it into the key
    ax.annotate("marker area = observations in the bin", xy=(0.98, 0.04),
                xycoords="axes fraction", ha="right", fontsize=7.5, color="#6b7280")
    return save(fig, name)


def skill_plot(hours, model_ll, market_ll, name="model_vs_market.png"):
    fig, ax = plt.subplots(figsize=(5.6, 4.0))
    ax.plot(hours, market_ll, "-o", color=MARKET, linewidth=2, markersize=5, label="market")
    ax.plot(hours, model_ll, "-o", color=MODEL, linewidth=2, markersize=5, label="model")
    _style(ax, "Log loss by decision hour (lower is better)",
           "local hour", "log loss on the settled bucket")
    ax.legend(fontsize=8, frameon=False)
    return save(fig, name)


def pnl_plot(curves, name="pnl_curve.png", marker_day=None, marker_label=None):
    """curves: {label: [(day, cumulative P&L in dollars)]}

    x is parsed to real dates, not left as strings: the curves cover different
    day sets, and matplotlib's categorical axis would interleave them.
    """
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    colors = [MODEL, "#c9a227", "#2a9d8f"]
    styles = ["-", "--", ":"]
    for (label, curve), color, style in zip(curves.items(), colors, styles):
        xs = [date.fromisoformat(c[0]) for c in curve]
        ax.plot(xs, [c[1] for c in curve], style, linewidth=1.7,
                color=color, label=label)
    ax.axhline(0, color="#b9bec7", linestyle="--", linewidth=1)
    if marker_day:
        cut = date.fromisoformat(marker_day)
        ax.axvline(cut, color=WARN, linestyle=":", linewidth=1.4)
        ax.annotate(marker_label or marker_day, xy=(cut, ax.get_ylim()[0]),
                    xytext=(-5, 6), textcoords="offset points",
                    fontsize=8, color=WARN, ha="right", va="bottom")
    _style(ax, "Cumulative P&L of the gated strategy, flat $1 per ticket",
           "", "cumulative P&L (USD)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=9))
    ax.tick_params(axis="x", rotation=30)
    ax.legend(fontsize=8, frameon=False, loc="lower left")
    return save(fig, name)
