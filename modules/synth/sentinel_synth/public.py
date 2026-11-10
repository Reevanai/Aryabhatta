"""Public interface of the synthetic data generator."""

from sentinel_synth.config import PROFILES, SCENARIO_TYPES, ScenarioCounts, SynthConfig, profile
from sentinel_synth.generator import GENERATOR_VERSION, generate
from sentinel_synth.world import World
from sentinel_synth.writer import DATASETS, LOAD_ORDER, labels, rows, stats, write

__all__ = [
    "DATASETS",
    "GENERATOR_VERSION",
    "LOAD_ORDER",
    "PROFILES",
    "SCENARIO_TYPES",
    "ScenarioCounts",
    "SynthConfig",
    "World",
    "generate",
    "labels",
    "profile",
    "rows",
    "stats",
    "write",
]
