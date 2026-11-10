"""Builds a complete synthetic data set from a configuration."""

from sentinel_synth import behaviour, fraud, population
from sentinel_synth.config import SynthConfig
from sentinel_synth.world import World

GENERATOR_VERSION = "0.3.0"


def generate(config: SynthConfig) -> World:
    """Deterministic for a given configuration: the same seed gives the same data."""
    world = World(config)
    if config.institution == "nbfc":
        population.populate_nbfc(world)
    else:
        population.populate_bank(world)
        employers = [i for i, p in enumerate(world.parties) if p.kind == "organisation"]
        behaviour.income(world, employers)
        behaviour.spending(world)
        behaviour.quirks(world)
    behaviour.loan_servicing(world)
    fraud.inject(world)
    world.finalise()
    return world
