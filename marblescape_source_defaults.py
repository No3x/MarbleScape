"""Default selections shared by runtime configuration and the Settings dialog."""

from copy import deepcopy

from marblescape_copernicus import DEFAULT_PROFILE as DEFAULT_COPERNICUS_PROFILE
from marblescape_eumetsat import DEFAULT_PROFILE as DEFAULT_EUMETSAT_PROFILE
from marblescape_worldview import DEFAULT_PROFILE as DEFAULT_WORLDVIEW_PROFILE


DEFAULT_SOURCE_PROFILES = {
    "eumetsat": dict(DEFAULT_EUMETSAT_PROFILE),
    "goes_east": {"area": "full_disk", "product": "GEOCOLOR", "resolution": "auto"},
    "goes_west": {"area": "full_disk", "product": "GEOCOLOR", "resolution": "auto"},
    "solar": {"area": "sun", "product": "Fe171", "resolution": "auto"},
    "himawari": {"area": "nict_full_disk", "product": "true_color", "resolution": "auto"},
    "slider": {"area": "goes-19---full_disk", "product": "geocolor", "resolution": "auto"},
    "copernicus": dict(DEFAULT_COPERNICUS_PROFILE),
    "worldview": dict(DEFAULT_WORLDVIEW_PROFILE),
}

AUTO_RESOLUTION_PROVIDERS = tuple(
    provider for provider, profile in DEFAULT_SOURCE_PROFILES.items()
    if "resolution" in profile
)


def default_source_profiles():
    return deepcopy(DEFAULT_SOURCE_PROFILES)
