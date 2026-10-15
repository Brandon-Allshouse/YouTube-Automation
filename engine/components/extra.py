"""Registry of data/visual components beyond the core text set (charts, maps, diagrams, ...).

``engine.build`` merges ``REGISTRY`` into its component table.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from engine.components.ambient import IconScene, ParticleBackground
from engine.components.charts import BarRace, LineChartReveal
from engine.components.comparison import ComparisonSplit
from engine.components.diagrams import DiagramBuild, Timeline
from engine.components.maps import AnimatedMap

REGISTRY: dict[str, Callable[..., Any]] = {
    "ComparisonSplit": ComparisonSplit,
    "AnimatedMap": AnimatedMap,
    "LineChartReveal": LineChartReveal,
    "BarRace": BarRace,
    "Timeline": Timeline,
    "DiagramBuild": DiagramBuild,
    "ParticleBackground": ParticleBackground,
    "IconScene": IconScene,
}
