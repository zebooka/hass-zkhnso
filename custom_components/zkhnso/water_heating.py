"""Water heating rate options and the Novosibirsk regional rate table."""

SYSTEMS = ("closed_external", "closed_internal", "open_internal")
BUILDING_FEATURES = (
    "uninsulated_with_towel_rails",
    "insulated_with_towel_rails",
    "uninsulated_without_towel_rails",
    "insulated_without_towel_rails",
)
RATE_TYPES = ("none", "auto", "manual")

"""Приложение к приказу департамента по тарифам Новосибирской области от 12.07.2022 № 131-B"""
_RATES = (
    (0.06779, 0.06528, 0.06528),
    (0.06277, 0.06026, 0.06026),
    (0.06277, 0.06026, 0.06026),
    (0.05775, 0.05524, 0.05524),
)


def water_heating_rate(system: str, features: str) -> float:
    """Return the tabulated rate in Gcal/m³, rejecting unknown choices."""
    return _RATES[BUILDING_FEATURES.index(features)][SYSTEMS.index(system)]
