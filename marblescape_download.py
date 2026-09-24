#!/usr/bin/env python3

from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import argparse
import calendar
from copy import deepcopy
import ctypes
import datetime as dt
import http.client
import hashlib
import io
import json
import math
import os
import re
import ssl
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import uuid
import webbrowser
import xml.etree.ElementTree as ET

from marblescape_noaa import NOAAClient
from marblescape_himawari import HimawariClient
from marblescape_slider import SliderClient
from marblescape_worldview import WorldviewClient
from marblescape_eumetsat import (
    normalize_profile as normalize_eumetsat_profile,
    supports_gap_fill as eumetsat_supports_gap_fill,
)
from marblescape_catalogues import CatalogueClient
from marblescape_copernicus import (
    CopernicusClient,
    MAX_CATALOGUE_DATES,
    PROCESS_URL as COPERNICUS_PROCESS_URL,
    catalogue_revision as copernicus_catalogue_revision,
    get_layer as get_copernicus_layer,
    get_product as get_copernicus_product,
    normalize_auth_configuration,
    normalize_profile as normalize_copernicus_profile,
    protect_client_secret,
)
from marblescape_profiles import (
    RotationScheduler,
    normalize_library,
    serialize_library,
)
from marblescape_cache import get_profile_image_cache, signature_digest
from marblescape_source_defaults import (
    AUTO_RESOLUTION_PROVIDERS,
    default_source_profiles,
)
from marblescape_download_progress import (
    DOWNLOAD_PROGRESS,
    DownloadCancelledError,
    read_response,
)
from marblescape_time import (
    TIME_ZONE_MENU_CHOICES,
    format_display_datetime,
    normalize_time_zone,
)


# =============================================================================
# CONFIGURATION
# =============================================================================

# This file contains safe defaults. If marblescape_config.toml exists next to the
# application, its values take precedence so users do not need to edit Python
# code. Frozen builds use the executable directory instead of the temporary
# bundle directory.
if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", SCRIPT_DIR)).resolve()
else:
    SCRIPT_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = SCRIPT_DIR

# Ordered from bottom to top. Kinds "basemap" and "overlay" accept the friendly
# Friendly EUMETSAT names are handled below; kind "wms" uses an exact capabilities name.
LAYER_CONFIG = [
    {
        "kind": "basemap",
        "name": "Natural Earth",
        "enabled": True,
        "opacity": 1.0,
        "style": "",
    },
    {
        "kind": "wms",
        "name": "mtg_fd:rgb_geocolour",
        "enabled": True,
        "opacity": 1.0,
        "style": "",
    },
]

# "auto" uses one efficient WMS request unless per-layer opacity/time requires
# local composition. "server" always uses one request; "local" always requests
# transparent PNG layers separately and combines them with Pillow.
RENDER_MODE = "auto"

# Map projection.
# Supported values:
#   "Geographic"
#   "GEOS: MSG FES, MTG FD"
#   "GEOS: MSG RSS"
#   "GEOS: MSG IODC"
#   "Spherical Mercator"
#   "North Polar"
#   "South Polar"
PROJECTION = "GEOS: MSG FES, MTG FD"

# Built-in view name. Supported values are defined in VIEW_PRESETS below.
# "custom" uses CUSTOM_BBOX in logical x/y coordinate order.
VIEW_PRESET = "full_earth"
CUSTOM_BBOX = None

# Output aspect ratio.
# Examples: "16:9", "3:2", "4:3", "1:1", "21:9".
# Set to None to derive the ratio from WIDTH and HEIGHT.
ASPECT_RATIO = "16:9"

# Output image size.
# If HEIGHT is None, it is calculated from WIDTH and ASPECT_RATIO.
WIDTH = 2560
HEIGHT = None

# WMS supersampling factor. Values above 1.0 render at a larger intermediate
# size and downsample to the configured output dimensions with Lanczos.
RENDER_SCALE = 1.0
RENDER_SCALE_AUTOMATIC = True

# View behavior when the requested aspect ratio differs from the projection's
# base extent.
#   "fit"  keeps the full base extent visible and adds surrounding map space.
#   "crop" fills the output frame by cropping the base extent.
VIEW_MODE = "fit"

# Map zoom factor.
#   1.0  = base view
#   >1.0 = zoom in
#   <1.0 = zoom out
DEFAULT_ZOOM = 1.1
ZOOM = DEFAULT_ZOOM

# When enabled, MTG TrueColor shows only the sunlit area. The remainder of
# the Earth disk is filled with black while the area outside the disk keeps
# the configured background color.
TRUECOLOR_BLACK_NIGHT = False

# Polling interval for checking the latest image.
UPDATE_INTERVAL_MINUTES = 5.0

# True keeps the process running and checking at UPDATE_INTERVAL_MINUTES.
# False performs one check and exits.
RUN_CONTINUOUSLY = True

# Global display-only time zone. Provider/cache timestamps remain in UTC.
DISPLAY_TIME_ZONE = "system"

# Root output directories. The script selects the path for the current OS.
OUTPUT_ROOT_WINDOWS = SCRIPT_DIR
OUTPUT_ROOT_LINUX = SCRIPT_DIR

# The script creates these subdirectories below the selected output root.
CONTENT_DIRECTORY_NAME = "content"
LATEST_DIRECTORY_NAME = "latest"
HISTORY_DIRECTORY_NAME = "history"
CUSTOM_LATEST_FOLDER = ""
CUSTOM_HISTORY_FOLDER = ""

# File-name prefix for the current image. Each changed image receives a new
# name and can be set directly as the Windows desktop wallpaper.
LATEST_FILENAME_PREFIX = "marblescape"
LATEST_STATE_FILENAME = ".marblescape-latest.json"

# History settings.
ENABLE_HISTORY = False
HISTORY_FILENAME_PREFIX = "marblescape"

# History retention mode:
#   "count" keeps at most HISTORY_MAX_FILES files.
#   "time" keeps files newer than the configured retention period.
#   "both" applies both limits; whichever removes a file first wins.
HISTORY_RETENTION_MODE = "count"

# Maximum number of archived files when count-based retention is enabled.
HISTORY_MAX_FILES = 100

# Maximum archive age when time-based retention is enabled.
HISTORY_RETENTION_YEARS = 0
HISTORY_RETENTION_MONTHS = 0
HISTORY_RETENTION_DAYS = 1
HISTORY_RETENTION_HOURS = 0
HISTORY_RETENTION_MINUTES = 0

# Optional fixed image time in ISO 8601 format.
# None requests the latest image currently available from EUMETSAT.
IMAGE_TIME = None

# Background color used outside rendered map content.
BACKGROUND_COLOR = "#000000"

# Network timeout in seconds.
NETWORK_TIMEOUT_SECONDS = 90

# Download display settings. Transfer sizes come from streamed HTTP response
# bytes; a percentage is shown only when the complete size is known.
DEFAULT_SHOW_DOWNLOAD_SPEED = True
DEFAULT_DOWNLOAD_SPEED_UNIT = "automatic"
DEFAULT_SHOW_DOWNLOAD_PROGRESS = True
DEFAULT_SHOW_DOWNLOAD_PROGRESS_BAR = True
DEFAULT_KEEP_COMPLETED_DOWNLOAD_VISIBLE = False
DEFAULT_DOWNLOAD_RETRIES = 2
DEFAULT_CATALOGUE_RETRIES = 2
DEFAULT_PROFILE_LIST_COLUMNS = (
    "name", "source", "selection", "time", "location", "latitude", "longitude",
    "coverage",
)
SHOW_DOWNLOAD_SPEED = DEFAULT_SHOW_DOWNLOAD_SPEED
DOWNLOAD_SPEED_UNIT = DEFAULT_DOWNLOAD_SPEED_UNIT
SHOW_DOWNLOAD_PROGRESS = DEFAULT_SHOW_DOWNLOAD_PROGRESS
SHOW_DOWNLOAD_PROGRESS_BAR = DEFAULT_SHOW_DOWNLOAD_PROGRESS_BAR
KEEP_COMPLETED_DOWNLOAD_VISIBLE = DEFAULT_KEEP_COMPLETED_DOWNLOAD_VISIBLE
DOWNLOAD_RETRIES = DEFAULT_DOWNLOAD_RETRIES
CATALOGUE_RETRIES = DEFAULT_CATALOGUE_RETRIES
PROFILE_LIST_VISIBLE_COLUMNS = DEFAULT_PROFILE_LIST_COLUMNS
DOWNLOAD_SPEED_UNITS = ("automatic", "KB/s", "MB/s", "Mbit/s")
DOWNLOAD_SPEED_UNIT_CHOICES = ("Automatic", "KB/s", "MB/s", "Mbit/s")

# Request headers used for WMS requests.
from app_version import VERSION

USER_AGENT = f"MarbleScapeWallpaperDownloader/{VERSION}"
PROJECT_URL = "https://github.com/Gittegatt/MarbleScape"
GITHUB_LATEST_RELEASE_API = (
    "https://api.github.com/repos/Gittegatt/MarbleScape/releases/latest"
)
GITHUB_TAGS_API = "https://api.github.com/repos/Gittegatt/MarbleScape/tags?per_page=1"
MAX_GITHUB_RESPONSE_BYTES = 1_000_000
SOURCE_VIEWER_URLS = (
    ("EUMETSAT", (
        "https://view.eumetsat.int/productviewer",
    )),
    ("NOAA STAR - GOES-East and GOES-West", (
        "https://www.star.nesdis.noaa.gov/goes/index.php",
    )),
    ("NOAA STAR - Solar (SUVI)", (
        "https://www.star.nesdis.noaa.gov/goes/SUVI.php?sat=G19",
    )),
    ("Himawari - NICT", (
        "https://himawari8.nict.go.jp/",
    )),
    ("Himawari - JMA", (
        "https://ds.data.jma.go.jp/mscweb/data/himawari/index.html",
    )),
    ("CIRA SLIDER", (
        "https://slider.cira.colostate.edu/",
    )),
    ("NASA Worldview", (
        "https://worldview.earthdata.nasa.gov/",
    )),
    ("Copernicus Browser", (
        "https://browser.dataspace.copernicus.eu/",
    )),
)
BEST_PRACTICE_TEXT = (
    "The easiest way to create the satellite view you want is to begin with the "
    "original browser for that image source. Open the source website from the "
    "Sources tab in MarbleScape, explore its available views, and adjust the "
    "visualization until the imagery matches your intended result. This gives you "
    "a clear preview of the source data before you configure the wallpaper.\n\n"
    "Then transfer the relevant choices to MarbleScape: image source, satellite, "
    "mission, product, layer, projection, area, custom latitude and longitude, zoom, "
    "date, coverage options, and any other source-specific settings. Under General > "
    "Output, configure the width, height, and aspect ratio for your monitor. Set the "
    "fit mode and wallpaper position for your "
    "monitor. Select Apply, review the resulting wallpaper, and refine the settings "
    "if necessary. Once the result is satisfactory, save the current Image settings "
    "as a profile under Profiles & Rotation so the same view can be restored or "
    "included in a rotation.\n\n"
    "For cleaner edges, finer details, and fewer visible stair-step artifacts, choose "
    "a source resolution one available size above the required output when bandwidth "
    "and provider limits allow it. Automatic remains the efficient starting point.\n\n"
    "Provider websites and MarbleScape can use slightly different labels or expose "
    "different subsets of the same catalogue. Use the actual satellite view and "
    "geographic result as the reference when matching settings. Satellite imagery can "
    "contain seams, processing artifacts, and areas with missing or partial imagery."
)

# Each image provider keeps its own selection; old configurations use EUMETSAT.
IMAGE_SOURCE = "eumetsat"
DEFAULT_SOURCE_PROFILES = default_source_profiles()
SOURCE_PROFILES = {key: dict(value) for key, value in DEFAULT_SOURCE_PROFILES.items()}
CHECK_FOR_SOURCE_UPDATES = True
SOURCE_LABELS = {
    "eumetsat": "EUMETSAT", "goes_east": "GOES-East",
    "goes_west": "GOES-West", "solar": "Solar / Sun (SUVI)",
    "himawari": "Himawari",
    "slider": "CIRA SLIDER",
    "copernicus": "Copernicus Browser",
    "worldview": "NASA Worldview",
}
IMAGE_STATUS_LOCK = threading.Lock()
IMAGE_STATUS = {"provider": None, "timestamp": None, "error": "", "interval_minutes": 10}
NOAA_CLIENTS = {}
NOAA_CLIENTS_LOCK = threading.Lock()
HIMAWARI_CLIENTS = {}
HIMAWARI_CLIENTS_LOCK = threading.Lock()
SLIDER_CLIENTS = {}
SLIDER_CLIENTS_LOCK = threading.Lock()
WORLDVIEW_CLIENTS = {}
WORLDVIEW_CLIENTS_LOCK = threading.Lock()
CATALOGUE_CLIENTS = {}
CATALOGUE_CLIENTS_LOCK = threading.Lock()
COPERNICUS_CLIENT_ID = ""
COPERNICUS_CLIENT_SECRET = ""
COPERNICUS_CLIENT_SECRET_PROTECTED = ""
COPERNICUS_CLIENTS = {}
COPERNICUS_CLIENTS_LOCK = threading.Lock()
IMAGE_PROFILE_LIBRARY = normalize_library({})
ROTATION_STATUS_LOCK = threading.Lock()
ROTATION_STATUS = {"text": "Rotation is disabled.", "deadline": None,
                   "active_profile_id": None}
_ROTATION_ACTIVE_UNCHANGED = object()
NEXT_ROTATION_DEADLINE = None
CURRENT_IMAGE_LOCK = threading.Lock()
CURRENT_IMAGE_PATH = None
ACTIVE_PROFILE_CACHE_ID = None
HISTORY_LOCK = threading.RLock()

# Optional TOML file next to this script. Pass --config to select another file.
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "marblescape_config.toml"
DEFAULT_CONFIG_TEMPLATE_PATH = SCRIPT_DIR / "marblescape_config.example.toml"
ACTIVE_CONFIG_PATH = DEFAULT_CONFIG_PATH
PROFILE_LIBRARY_PATH = SCRIPT_DIR / "profiles.toml"
ACTIVE_PROFILE_LIBRARY_PATH = PROFILE_LIBRARY_PATH

# If True on Windows, set every newly downloaded image directly as the desktop
# wallpaper. This avoids slideshow scheduling and file-cache delays.
SET_WINDOWS_WALLPAPER = True

# Windows wallpaper positioning.
# Supported values: "center", "tile", "stretch", "fit", "fill", "span", "none".
WINDOWS_WALLPAPER_POSITION = "fit"
WINDOWS_WALLPAPER_MONITOR_POSITIONS = {}
WINDOWS_WALLPAPER_MONITOR_OUTPUTS = {}
WINDOWS_WALLPAPER_LOCK = threading.RLock()
WINDOWS_SINGLE_INSTANCE_HANDLE = None
SKIPPED_UPDATE_VERSION = ""

# Keep the console open after a fatal error when launched by double-click on
# Windows. This has no effect on Linux or Docker.
WINDOWS_PAUSE_ON_EXIT = True


# =============================================================================
# INTERNAL CONSTANTS
# =============================================================================

WMS_URL = "https://view.eumetsat.int/geoserver/wms"
WMS_VERSION = "1.3.0"
IMAGE_FORMAT = "image/png"
MAX_WMS_DIMENSION = 4000
TRUECOLOR_LAYER_NAME = "mtg_fd:rgb_truecolour"
TRUECOLOR_EARTH_MASK_LAYER = "backgrounds:ne_gray"

OUTPUT_ROOT = OUTPUT_ROOT_WINDOWS if os.name == "nt" else OUTPUT_ROOT_LINUX
CONTENT_DIR = OUTPUT_ROOT / CONTENT_DIRECTORY_NAME
LATEST_DIR = CONTENT_DIR / LATEST_DIRECTORY_NAME
HISTORY_DIR = CONTENT_DIR / HISTORY_DIRECTORY_NAME

KNOWN_OVERLAYS = {
    "Coastlines": "backgrounds:ne_10m_coastline",
    "Labels (dark)": "osmgray:dark_labels",
    "Labels (light)": "osmgray:light_labels",
}

# CRS and new-mode extents follow the EUMETSAT viewer configuration:
# https://view.eumetsat.int/assets/data/config.json
# Keep the existing GEOS extents to preserve established framing.
PROJECTIONS = {
    "Geographic": {
        "crs": "EPSG:4326",
        "xmin": -180.0,
        "xmax": 180.0,
        "ymin": -90.0,
        "ymax": 90.0,
        "axis_order": "yx",
    },
    "GEOS: MSG FES, MTG FD": {
        "crs": "AUTO:97004,9001,0,0",
        "xmin": -6500000.0,
        "xmax": 6500000.0,
        "ymin": -6500000.0,
        "ymax": 6500000.0,
        "axis_order": "xy",
    },
    "GEOS: MSG RSS": {
        "crs": "AUTO:97004,9001,9.5,0",
        "xmin": -6500000.0,
        "xmax": 6500000.0,
        "ymin": -6500000.0,
        "ymax": 6500000.0,
        "axis_order": "xy",
    },
    "GEOS: MSG IODC": {
        "crs": "AUTO:97004,9001,41.5,0",
        "xmin": -5440000.0,
        "xmax": 5440000.0,
        "ymin": -5440000.0,
        "ymax": 5440000.0,
        "axis_order": "xy",
    },
    "Spherical Mercator": {
        "crs": "EPSG:3857",
        "xmin": -20037508.342789244,
        "xmax": 20037508.342789244,
        "ymin": -20037508.342789244,
        "ymax": 20037508.342789244,
        "axis_order": "xy",
    },
    "North Polar": {
        "crs": "EPSG:3995",
        "xmin": -12700000.0,
        "xmax": 12700000.0,
        "ymin": -12700000.0,
        "ymax": 12700000.0,
        "axis_order": "xy",
    },
    "South Polar": {
        "crs": "EPSG:3976",
        "xmin": -12700000.0,
        "xmax": 12700000.0,
        "ymin": -12700000.0,
        "ymax": 12700000.0,
        "axis_order": "xy",
    },
}

def available_projection_choices():
    """Return all projections published by the official EUMETSAT viewer config."""
    return tuple(PROJECTIONS)


# Geographic preset extents use logical x/y order: west, south, east, north.
# Regional presets deliberately use EPSG:4326 because those coordinates are
# understandable and editable without an additional projection library.
VIEW_PRESETS = {
    "full_earth": {"projection": None, "bbox": None},
    "europe": {
        "projection": "Geographic",
        "bbox": (-25.0, 30.0, 45.0, 72.0),
    },
    "mediterranean": {
        "projection": "Geographic",
        "bbox": (-12.0, 28.0, 42.0, 48.0),
    },
    "central_europe": {
        "projection": "Geographic",
        "bbox": (-2.0, 43.0, 25.0, 57.0),
    },
    "custom": {"projection": None, "bbox": None},
}

WINDOWS_WALLPAPER_POSITIONS = {
    "center": 0,
    "tile": 1,
    "stretch": 2,
    "fit": 3,
    "fill": 4,
    "span": 5,
}
WALLPAPER_POSITION_CHOICES = (*WINDOWS_WALLPAPER_POSITIONS, "none")
WALLPAPER_POSITION_LABELS = {
    **{name: name for name in WINDOWS_WALLPAPER_POSITIONS},
    "none": "Do not update (keep current wallpaper)",
}

APPLICATION_STOP_EVENT = threading.Event()
FORCE_UPDATE_EVENT = threading.Event()
CONFIGURATION_RELOAD_EVENT = threading.Event()
CONFIGURATION_FILE_LOCK = threading.RLock()
LOADED_CONFIGURATION_FIELDS = (
    "IMAGE_SOURCE", "SOURCE_PROFILES", "CHECK_FOR_SOURCE_UPDATES", "IMAGE_PROFILE_LIBRARY",
    "COPERNICUS_CLIENT_ID", "COPERNICUS_CLIENT_SECRET",
    "COPERNICUS_CLIENT_SECRET_PROTECTED",
    "WMS_URL", "WMS_VERSION", "IMAGE_TIME", "NETWORK_TIMEOUT_SECONDS",
    "SHOW_DOWNLOAD_SPEED", "DOWNLOAD_SPEED_UNIT", "SHOW_DOWNLOAD_PROGRESS",
    "SHOW_DOWNLOAD_PROGRESS_BAR", "KEEP_COMPLETED_DOWNLOAD_VISIBLE",
    "DOWNLOAD_RETRIES", "CATALOGUE_RETRIES",
    "PROFILE_LIST_VISIBLE_COLUMNS",
    "UPDATE_INTERVAL_MINUTES", "RUN_CONTINUOUSLY", "RENDER_MODE",
    "WIDTH", "HEIGHT", "ASPECT_RATIO", "BACKGROUND_COLOR", "RENDER_SCALE",
    "RENDER_SCALE_AUTOMATIC", "OUTPUT_ROOT_WINDOWS", "OUTPUT_ROOT_LINUX",
    "CUSTOM_LATEST_FOLDER", "CUSTOM_HISTORY_FOLDER", "PROJECTION",
    "VIEW_PRESET", "CUSTOM_BBOX", "VIEW_MODE", "ZOOM",
    "TRUECOLOR_BLACK_NIGHT", "ENABLE_HISTORY", "HISTORY_RETENTION_MODE",
    "HISTORY_MAX_FILES", "HISTORY_RETENTION_YEARS",
    "HISTORY_RETENTION_MONTHS", "HISTORY_RETENTION_DAYS",
    "HISTORY_RETENTION_HOURS", "HISTORY_RETENTION_MINUTES",
    "SET_WINDOWS_WALLPAPER", "WINDOWS_WALLPAPER_POSITION",
    "WINDOWS_WALLPAPER_MONITOR_POSITIONS", "WINDOWS_WALLPAPER_MONITOR_OUTPUTS",
    "WINDOWS_PAUSE_ON_EXIT", "DISPLAY_TIME_ZONE", "LAYER_CONFIG", "ACTIVE_CONFIG_PATH",
    "ACTIVE_PROFILE_LIBRARY_PATH", "SKIPPED_UPDATE_VERSION",
)
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_RUN_VALUE_NAME = "MarbleScape"
SETTINGS_BACKUP_FORMAT = "marblescape-settings-backup"
SETTINGS_BACKUP_VERSION = 2
MAX_BACKUP_CONFIGURATION_BYTES = 1_000_000
MAX_SETTINGS_BACKUP_FILE_BYTES = 5_000_000

VIEW_PRESET_MENU_CHOICES = (
    ("Full Earth", "full_earth"),
    ("Europe", "europe"),
    ("Mediterranean", "mediterranean"),
    ("Central Europe", "central_europe"),
)

# Selecting a preset applies a complete, predictable starting profile. Users
# can still change individual settings afterwards without changing the preset.
VIEW_PRESET_PROFILES = {
    "full_earth": {
        "satellite_layer": "mtg_fd:rgb_geocolour",
        "projection": "GEOS: MSG FES, MTG FD",
        "fit_mode": "fit",
        "zoom": DEFAULT_ZOOM,
    },
    "europe": {
        "satellite_layer": "mtg_fd:rgb_geocolour",
        "projection": "Geographic",
        "fit_mode": "fit",
        "zoom": DEFAULT_ZOOM,
    },
    "mediterranean": {
        "satellite_layer": "mtg_fd:rgb_geocolour",
        "projection": "Geographic",
        "fit_mode": "fit",
        "zoom": DEFAULT_ZOOM,
    },
    "central_europe": {
        "satellite_layer": "mtg_fd:rgb_geocolour",
        "projection": "Geographic",
        "fit_mode": "fit",
        "zoom": DEFAULT_ZOOM,
    },
}

SATELLITE_LAYER_MENU_CHOICES = (
    ("MTG GeoColor", "mtg_fd:rgb_geocolour"),
    ("MTG TrueColor (day)", "mtg_fd:rgb_truecolour"),
    ("MTG Cloud Phase (day)", "mtg_fd:rgb_cloudphase"),
    ("MTG Cloud Type (day)", "mtg_fd:rgb_cloudtype"),
    ("MTG Dust", "mtg_fd:rgb_dust"),
    ("MTG Fog / Low Clouds (night)", "mtg_fd:rgb_fog"),
    ("MSG Natural Color Enhanced (day)", "msg_fes:rgb_naturalenhncd"),
)

WALLPAPER_POSITION_MENU_CHOICES = (
    ("Fit", "fit"),
    ("Fill", "fill"),
    ("Stretch", "stretch"),
    ("Center", "center"),
    ("Tile", "tile"),
    ("Span", "span"),
)

FIT_MODE_MENU_CHOICES = (
    ("Fit", "fit"),
    ("Crop", "crop"),
)

ZOOM_MENU_CHOICES = (
    ("0.8x", 0.8),
    ("1.0x", 1.0),
    ("1.2x", 1.2),
    ("1.5x", 1.5),
    ("2.0x", 2.0),
)

OUTPUT_SIZE_MENU_GROUPS = (
    (
        "16:9 widescreen",
        (
            ("1280 x 720 (HD)", 1280, 720),
            ("1360 x 768 (HD)", 1360, 768),
            ("1366 x 768 (HD)", 1366, 768),
            ("1600 x 900 (HD+)", 1600, 900),
            ("1920 x 1080 (Full HD)", 1920, 1080),
            ("2160 x 1215", 2160, 1215),
            ("2560 x 1440 (QHD)", 2560, 1440),
            ("3200 x 1800 (QHD+)", 3200, 1800),
            ("3840 x 2160 (4K UHD)", 3840, 2160),
            ("5120 x 2880 (5K)", 5120, 2880),
            ("7680 x 4320 (8K UHD)", 7680, 4320),
        ),
    ),
    (
        "16:10",
        (
            ("1280 x 800", 1280, 800),
            ("1440 x 900", 1440, 900),
            ("1680 x 1050", 1680, 1050),
            ("1920 x 1200 (WUXGA)", 1920, 1200),
            ("2560 x 1600", 2560, 1600),
            ("2880 x 1800", 2880, 1800),
            ("3840 x 2400", 3840, 2400),
        ),
    ),
    (
        "3:2",
        (
            ("1920 x 1280", 1920, 1280),
            ("2160 x 1440", 2160, 1440),
            ("2256 x 1504", 2256, 1504),
            ("2304 x 1536", 2304, 1536),
            ("2496 x 1664", 2496, 1664),
            ("3000 x 2000", 3000, 2000),
            ("3240 x 2160", 3240, 2160),
        ),
    ),
    (
        "Classic",
        (
            ("1024 x 768 (4:3)", 1024, 768),
            ("1280 x 960 (4:3)", 1280, 960),
            ("1600 x 1200 (4:3)", 1600, 1200),
            ("1280 x 1024 (5:4)", 1280, 1024),
        ),
    ),
    (
        "Ultrawide",
        (
            ("2560 x 1080 (~21:9)", 2560, 1080),
            ("3440 x 1440 (~21:9)", 3440, 1440),
            ("3840 x 1600 (~21:9)", 3840, 1600),
            ("5120 x 2160 (5K2K)", 5120, 2160),
        ),
    ),
    (
        "Super ultrawide",
        (
            ("3840 x 1080 (32:9)", 3840, 1080),
            ("5120 x 1440 (32:9)", 5120, 1440),
            ("7680 x 2160 (32:9)", 7680, 2160),
        ),
    ),
    (
        "Portrait",
        (
            ("1080 x 1920 (9:16)", 1080, 1920),
            ("1200 x 1920 (10:16)", 1200, 1920),
            ("1440 x 2560 (9:16)", 1440, 2560),
            ("2160 x 3840 (9:16)", 2160, 3840),
        ),
    ),
)

OUTPUT_SIZE_MENU_CHOICES = tuple(
    choice
    for _group_label, group_choices in OUTPUT_SIZE_MENU_GROUPS
    for choice in group_choices
)

ASPECT_RATIO_MENU_GROUPS = (
    (
        "Standard",
        (
            ("16:9", "16:9"),
            ("16:10", "16:10"),
            ("3:2", "3:2"),
            ("4:3", "4:3"),
            ("5:4", "5:4"),
            ("1:1", "1:1"),
        ),
    ),
    (
        "Ultrawide",
        (
            ("2:1 (18:9)", "2:1"),
            ("21:9 (7:3)", "21:9"),
            ("64:27 (~21:9)", "64:27"),
            ("43:18 (~21:9)", "43:18"),
            ("24:10 (12:5)", "12:5"),
            ("32:10 (16:5)", "16:5"),
            ("32:9", "32:9"),
        ),
    ),
    (
        "Portrait",
        (
            ("9:16", "9:16"),
            ("10:16 (5:8)", "5:8"),
            ("2:3", "2:3"),
            ("3:4", "3:4"),
            ("4:5", "4:5"),
        ),
    ),
)

ASPECT_RATIO_MENU_CHOICES = tuple(
    choice
    for _group_label, group_choices in ASPECT_RATIO_MENU_GROUPS
    for choice in group_choices
)

RENDER_QUALITY_MENU_CHOICES = (
    ("Auto (max useful)", "auto"),
    ("Standard (1.0×)", 1.0),
    ("High (1.25×)", 1.25),
    ("Very high (1.5×)", 1.5),
    ("Ultra (2.0×)", 2.0),
)

UPDATE_INTERVAL_MENU_CHOICES = (
    ("5 minutes", 5.0),
    ("10 minutes", 10.0),
    ("15 minutes", 15.0),
    ("30 minutes", 30.0),
    ("60 minutes", 60.0),
)

HISTORY_RETENTION_MODE_MENU_CHOICES = (
    ("Count", "count"),
    ("Time", "time"),
    ("Count and time", "both"),
)

HISTORY_MAX_FILES_MENU_CHOICES = (25, 50, 100, 250, 500)

HISTORY_MAX_AGE_MENU_CHOICES = (
    ("1 hour", {"years": 0, "months": 0, "days": 0, "hours": 1, "minutes": 0}),
    ("6 hours", {"years": 0, "months": 0, "days": 0, "hours": 6, "minutes": 0}),
    ("12 hours", {"years": 0, "months": 0, "days": 0, "hours": 12, "minutes": 0}),
    ("1 day", {"years": 0, "months": 0, "days": 1, "hours": 0, "minutes": 0}),
    ("7 days", {"years": 0, "months": 0, "days": 7, "hours": 0, "minutes": 0}),
    ("30 days", {"years": 0, "months": 0, "days": 30, "hours": 0, "minutes": 0}),
)


def refresh_output_paths():
    global OUTPUT_ROOT, CONTENT_DIR, LATEST_DIR, HISTORY_DIR
    OUTPUT_ROOT = OUTPUT_ROOT_WINDOWS if os.name == "nt" else OUTPUT_ROOT_LINUX
    CONTENT_DIR = OUTPUT_ROOT / CONTENT_DIRECTORY_NAME
    LATEST_DIR = (
        resolve_script_relative_path(CUSTOM_LATEST_FOLDER)
        if CUSTOM_LATEST_FOLDER else CONTENT_DIR / LATEST_DIRECTORY_NAME
    )
    HISTORY_DIR = (
        resolve_script_relative_path(CUSTOM_HISTORY_FOLDER)
        if CUSTOM_HISTORY_FOLDER else CONTENT_DIR / HISTORY_DIRECTORY_NAME
    )
    validate_image_folders(LATEST_DIR, HISTORY_DIR)


def validate_image_folders(latest, history):
    if latest.resolve() == history.resolve():
        raise ValueError("Latest and history folders must be different.")
    cache_directory = (OUTPUT_ROOT / CONTENT_DIRECTORY_NAME / "cache").resolve()
    cache_database = (OUTPUT_ROOT / CONTENT_DIRECTORY_NAME / "cache.sqlite3").resolve()
    for folder in (latest, history):
        resolved = folder.resolve()
        if resolved == cache_directory or cache_directory in resolved.parents:
            raise ValueError(
                "Latest and history folders cannot use the profile cache directory or its subfolders."
            )
        if resolved == cache_database or cache_database in resolved.parents:
            raise ValueError(
                "Latest and history folders cannot use the profile cache database path."
            )
        if folder.exists() and not folder.is_dir():
            raise ValueError(f"Image folder points to a file: {folder}")


# =============================================================================
# GENERAL HELPERS
# =============================================================================


def timestamp_text():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message=""):
    if message:
        print(f"[{timestamp_text()}] {message}", flush=True)
    else:
        print(flush=True)


def format_bytes(value):
    value = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    for unit in units:
        if abs(value) < 1024.0 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024.0


def format_disk_usage(value):
    megabytes = float(value) / 1_000_000.0
    if megabytes >= 1000.0:
        return f"{megabytes / 1000.0:.2f} GB"
    return f"{megabytes:.2f} MB"


def local_xml_name(tag):
    return tag.split("}")[-1]


def calculate_sha256(data):
    return hashlib.sha256(data).hexdigest()


def calculate_file_sha256(file_path):
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def get_profile_cache():
    return get_profile_image_cache(CONTENT_DIR)


def set_current_image_path(path, profile_id=None):
    global CURRENT_IMAGE_PATH, ACTIVE_PROFILE_CACHE_ID
    resolved = Path(path).resolve() if path is not None else None
    with CURRENT_IMAGE_LOCK:
        CURRENT_IMAGE_PATH = resolved
        ACTIVE_PROFILE_CACHE_ID = profile_id


def get_current_image_path():
    with CURRENT_IMAGE_LOCK:
        path = CURRENT_IMAGE_PATH
    try:
        return path if path is not None and path.is_file() else None
    except OSError:
        return None


def get_active_profile_cache_id():
    with CURRENT_IMAGE_LOCK:
        return ACTIVE_PROFILE_CACHE_ID


def clear_profile_image_cache():
    """Clear only profile-cache data and refresh an active rotated profile."""
    global CURRENT_IMAGE_PATH
    result = get_profile_cache().clear()
    with CURRENT_IMAGE_LOCK:
        active = ACTIVE_PROFILE_CACHE_ID is not None
        if active:
            CURRENT_IMAGE_PATH = None
    if active:
        FORCE_UPDATE_EVENT.set()
    return result


def synchronize_profile_image_cache():
    identifiers = [item["id"] for item in IMAGE_PROFILE_LIBRARY.get("items", ())]
    try:
        return get_profile_cache().retain_profiles(identifiers)
    except Exception as exc:
        log(f"Profile cache maintenance warning: {exc}")
        return {"profiles": 0, "files": 0}


def ensure_directories():
    LATEST_DIR.mkdir(parents=True, exist_ok=True)
    if ENABLE_HISTORY or not CUSTOM_HISTORY_FOLDER:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    get_profile_cache().images_dir.mkdir(parents=True, exist_ok=True)
    for stale_temp in LATEST_DIR.glob("*.tmp"):
        if stale_temp.is_file():
            stale_temp.unlink(missing_ok=True)


def resolve_script_relative_path(value):
    """Resolve relative configuration paths from the script directory."""
    expanded = os.path.expandvars(str(value))
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = SCRIPT_DIR / path
    return path.resolve()


def parse_render_scale_setting(value):
    """Return a numeric render scale or the persistent automatic mode."""
    if isinstance(value, str) and value.strip().lower() in {"auto", "automatic"}:
        return "auto"
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Render quality must be 'auto' or a numeric factor."
        ) from exc
    if not math.isfinite(numeric) or numeric < 1.0:
        raise ValueError("Render quality factor must be at least 1.0.")
    return numeric


def normalize_download_speed_unit(value):
    """Return the persistent spelling for a supported transfer-rate unit."""
    normalized = str(value).strip().casefold()
    aliases = {item.casefold(): item for item in DOWNLOAD_SPEED_UNITS}
    aliases.update({"auto": "automatic", "mbit/second": "Mbit/s"})
    if normalized not in aliases:
        raise ValueError("Download speed unit is invalid.")
    return aliases[normalized]


def normalize_download_retries(value):
    if type(value) is int and 1 <= value <= 9:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9]", value.strip()):
        return int(value.strip())
    raise ValueError("Download retries must be a whole number from 1 to 9.")


def normalize_catalogue_retries(value):
    if type(value) is int and 1 <= value <= 9:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9]", value.strip()):
        return int(value.strip())
    raise ValueError("Catalogue retries must be a whole number from 1 to 9.")


def format_download_speed(bytes_per_second, unit):
    """Format a byte rate in the user's selected decimal transfer unit."""
    speed = max(0.0, float(bytes_per_second))
    unit = normalize_download_speed_unit(unit)
    if unit == "automatic":
        if speed >= 1_000_000:
            unit = "MB/s"
        elif speed >= 1_000:
            unit = "KB/s"
        else:
            return f"{speed:.0f} B/s"
    if unit == "KB/s":
        return f"{speed / 1_000:.1f} KB/s"
    if unit == "MB/s":
        return f"{speed / 1_000_000:.2f} MB/s"
    return f"{speed * 8 / 1_000_000:.2f} Mbit/s"


def format_download_progress(snapshot, show_speed=True, speed_unit="automatic",
                             show_progress=True):
    """Format the optional settings-footer transfer status."""
    parts = []
    if show_progress:
        total = snapshot.get("total")
        transferred = int(snapshot.get("transferred", 0))
        if total is None:
            parts.append(f"{format_bytes(transferred)} downloaded · total size unknown")
        else:
            percent = float(snapshot.get("percent") or 0.0)
            parts.append(
                f"{percent:.0f}% · {format_bytes(transferred)} / {format_bytes(total)}"
            )
    if show_speed:
        parts.append(format_download_speed(snapshot.get("speed", 0.0), speed_unit))
    if not parts:
        return ""
    if snapshot.get("active") and snapshot.get("cancel_requested"):
        prefix = "Cancelling download"
    elif snapshot.get("active"):
        prefix = "Downloading"
    elif snapshot.get("cancelled"):
        prefix = "Download cancelled"
    else:
        prefix = "Downloaded" if snapshot.get("successful") else "Download interrupted"
    return prefix + ": " + " · ".join(parts)


def download_completion_text(snapshot):
    return "Completed." if (snapshot.get("visible") and snapshot.get("successful")
                            and not snapshot.get("active")) else ""


def version_tuple(value):
    """Return a comparable numeric version tuple for v1.2.3-style tags."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+){0,3})(?:[-+].*)?", str(value).strip())
    if not match:
        raise ValueError(f"Unsupported version tag: {value}")
    parts = tuple(int(part) for part in match.group(1).split("."))
    return parts + (0,) * (4 - len(parts))


def check_github_update(current_version=VERSION):
    """Read the newest public GitHub release, falling back to the newest tag."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    def read_json(url):
        request = Request(url, headers=headers)
        with urlopen(request, timeout=min(float(NETWORK_TIMEOUT_SECONDS), 30.0)) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_GITHUB_RESPONSE_BYTES:
                raise RuntimeError("GitHub returned an unexpectedly large response.")
            data = response.read(MAX_GITHUB_RESPONSE_BYTES + 1)
            if len(data) > MAX_GITHUB_RESPONSE_BYTES:
                raise RuntimeError("GitHub returned an unexpectedly large response.")
            return json.loads(data.decode("utf-8"))

    release_url = None
    try:
        payload = read_json(GITHUB_LATEST_RELEASE_API)
        tag = payload.get("tag_name")
        release_url = payload.get("html_url")
    except HTTPError as exc:
        if exc.code != 404:
            raise RuntimeError(f"GitHub update check failed with HTTP {exc.code}.") from exc
        try:
            tags = read_json(GITHUB_TAGS_API)
        except HTTPError as tag_error:
            if tag_error.code == 404:
                raise RuntimeError(
                    "No public GitHub release or version tag is accessible. "
                    "A private repository cannot be checked without GitHub authentication."
                ) from tag_error
            raise RuntimeError(
                f"GitHub update check failed with HTTP {tag_error.code}."
            ) from tag_error
        if not isinstance(tags, list) or not tags:
            raise RuntimeError(
                "No public GitHub release or version tag is available."
            ) from exc
        tag = tags[0].get("name")
        release_url = PROJECT_URL + "/releases"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GitHub update check failed: {exc}") from exc
    if not isinstance(tag, str) or not tag.strip():
        raise RuntimeError("GitHub returned no usable version tag.")
    if not isinstance(release_url, str) or not release_url.startswith(PROJECT_URL):
        release_url = PROJECT_URL + "/releases"
    return {
        "current": str(current_version),
        "latest": tag.strip(),
        "update_available": version_tuple(tag) > version_tuple(current_version),
        "url": release_url,
    }


def should_show_update_notification(result, skipped_version=""):
    """Show only a newer published release that has not been skipped."""
    if not result.get("update_available"):
        return False
    latest = result.get("latest", "")
    url = result.get("url", "")
    if not isinstance(url, str) or not url.startswith(PROJECT_URL + "/releases/tag/"):
        return False
    try:
        return not skipped_version or version_tuple(latest) != version_tuple(skipped_version)
    except ValueError:
        return True


def load_configuration(config_path):
    """Load optional user settings and update the script defaults."""
    global WMS_URL, WMS_VERSION, IMAGE_TIME, NETWORK_TIMEOUT_SECONDS
    global UPDATE_INTERVAL_MINUTES, RUN_CONTINUOUSLY, RENDER_MODE
    global WIDTH, HEIGHT, ASPECT_RATIO, BACKGROUND_COLOR, RENDER_SCALE
    global RENDER_SCALE_AUTOMATIC
    global OUTPUT_ROOT_WINDOWS, OUTPUT_ROOT_LINUX
    global CUSTOM_LATEST_FOLDER, CUSTOM_HISTORY_FOLDER
    global PROJECTION, VIEW_PRESET, CUSTOM_BBOX, VIEW_MODE, ZOOM
    global TRUECOLOR_BLACK_NIGHT
    global ENABLE_HISTORY, HISTORY_RETENTION_MODE, HISTORY_MAX_FILES
    global HISTORY_RETENTION_YEARS, HISTORY_RETENTION_MONTHS
    global HISTORY_RETENTION_DAYS, HISTORY_RETENTION_HOURS
    global HISTORY_RETENTION_MINUTES, SET_WINDOWS_WALLPAPER
    global WINDOWS_WALLPAPER_POSITION, WINDOWS_WALLPAPER_MONITOR_POSITIONS
    global WINDOWS_WALLPAPER_MONITOR_OUTPUTS
    global WINDOWS_PAUSE_ON_EXIT, LAYER_CONFIG
    global ACTIVE_CONFIG_PATH, ACTIVE_PROFILE_LIBRARY_PATH
    global IMAGE_SOURCE, SOURCE_PROFILES, CHECK_FOR_SOURCE_UPDATES, IMAGE_PROFILE_LIBRARY
    global COPERNICUS_CLIENT_ID, COPERNICUS_CLIENT_SECRET
    global COPERNICUS_CLIENT_SECRET_PROTECTED
    global DISPLAY_TIME_ZONE
    global SHOW_DOWNLOAD_SPEED, DOWNLOAD_SPEED_UNIT, SHOW_DOWNLOAD_PROGRESS
    global SHOW_DOWNLOAD_PROGRESS_BAR, KEEP_COMPLETED_DOWNLOAD_VISIBLE
    global DOWNLOAD_RETRIES, CATALOGUE_RETRIES, PROFILE_LIST_VISIBLE_COLUMNS
    global SKIPPED_UPDATE_VERSION

    config_path = resolve_script_relative_path(config_path)
    ACTIVE_CONFIG_PATH = config_path
    ACTIVE_PROFILE_LIBRARY_PATH = Path(PROFILE_LIBRARY_PATH).resolve()
    if not config_path.exists():
        if config_path != DEFAULT_CONFIG_PATH:
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        if DEFAULT_CONFIG_TEMPLATE_PATH.exists():
            initial_text = first_run_configuration_text(
                DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
            )
            temporary = config_path.with_name(
                f".{config_path.name}.{uuid.uuid4().hex}.tmp"
            )
            try:
                temporary.write_text(initial_text, encoding="utf-8", newline="")
                os.replace(temporary, config_path)
            finally:
                temporary.unlink(missing_ok=True)
            log(f"Created configuration from template: {config_path.name}")
        else:
            refresh_output_paths()
            return False

    with config_path.open("rb") as handle:
        config = tomllib.load(handle)

    if config_path.resolve() == DEFAULT_CONFIG_TEMPLATE_PATH.resolve():
        IMAGE_PROFILE_LIBRARY = normalize_library({})
    else:
        with CONFIGURATION_FILE_LOCK:
            if ACTIVE_PROFILE_LIBRARY_PATH.exists():
                IMAGE_PROFILE_LIBRARY = read_profile_library_file(
                    ACTIVE_PROFILE_LIBRARY_PATH
                )
            else:
                IMAGE_PROFILE_LIBRARY = normalize_library({})
                write_profile_library_file_unlocked(
                    IMAGE_PROFILE_LIBRARY, ACTIVE_PROFILE_LIBRARY_PATH
                )
    DISPLAY_TIME_ZONE = normalize_time_zone(
        config.get("display", {}).get("time_zone", "system")
    )

    source = config.get("source", {})
    if not isinstance(source, dict):
        raise ValueError("TOML 'source' must be a table.")
    if "check_for_updates" in source and type(source["check_for_updates"]) is not bool:
        raise ValueError("source.check_for_updates must be true or false.")
    CHECK_FOR_SOURCE_UPDATES = source.get("check_for_updates", True)
    IMAGE_SOURCE, SOURCE_PROFILES = normalize_source_configuration(
        source.get("provider", "eumetsat"),
        config.get("sources", {}),
    )
    (COPERNICUS_CLIENT_ID, COPERNICUS_CLIENT_SECRET,
     COPERNICUS_CLIENT_SECRET_PROTECTED) = normalize_auth_configuration(
        config.get("copernicus", {})
    )

    service = config.get("service", {})
    WMS_URL = str(service.get("endpoint", WMS_URL)).rstrip("?")
    WMS_VERSION = str(service.get("version", WMS_VERSION))
    IMAGE_TIME = service.get("time", IMAGE_TIME) or None
    NETWORK_TIMEOUT_SECONDS = service.get(
        "timeout_seconds", NETWORK_TIMEOUT_SECONDS
    )
    UPDATE_INTERVAL_MINUTES = service.get(
        "update_interval_minutes", UPDATE_INTERVAL_MINUTES
    )
    RUN_CONTINUOUSLY = service.get("run_continuously", RUN_CONTINUOUSLY)
    RENDER_MODE = str(service.get("render_mode", RENDER_MODE)).lower()

    download = config.get("download", {})
    if not isinstance(download, dict):
        raise ValueError("TOML 'download' must be a table.")
    for key in (
        "show_speed", "show_progress", "show_progress_bar",
        "keep_completed_visible",
    ):
        if key in download and type(download[key]) is not bool:
            raise ValueError(f"download.{key} must be true or false.")
    SHOW_DOWNLOAD_SPEED = download.get("show_speed", DEFAULT_SHOW_DOWNLOAD_SPEED)
    DOWNLOAD_SPEED_UNIT = normalize_download_speed_unit(
        download.get("speed_unit", DEFAULT_DOWNLOAD_SPEED_UNIT)
    )
    SHOW_DOWNLOAD_PROGRESS = download.get(
        "show_progress", DEFAULT_SHOW_DOWNLOAD_PROGRESS
    )
    SHOW_DOWNLOAD_PROGRESS_BAR = download.get(
        "show_progress_bar", DEFAULT_SHOW_DOWNLOAD_PROGRESS_BAR
    )
    KEEP_COMPLETED_DOWNLOAD_VISIBLE = download.get(
        "keep_completed_visible", DEFAULT_KEEP_COMPLETED_DOWNLOAD_VISIBLE
    )
    DOWNLOAD_RETRIES = normalize_download_retries(
        download.get("retries", DEFAULT_DOWNLOAD_RETRIES)
    )
    CATALOGUE_RETRIES = normalize_catalogue_retries(
        download.get("catalogue_retries", DEFAULT_CATALOGUE_RETRIES)
    )

    profile_list = config.get("profile_list", {})
    if not isinstance(profile_list, dict):
        raise ValueError("TOML 'profile_list' must be a table.")
    configured_columns = profile_list.get(
        "visible_columns", list(DEFAULT_PROFILE_LIST_COLUMNS)
    )
    if not isinstance(configured_columns, list):
        raise ValueError("profile_list.visible_columns must be a list.")
    if (
        not configured_columns
        or any(type(column) is not str for column in configured_columns)
        or len(set(configured_columns)) != len(configured_columns)
        or any(column not in DEFAULT_PROFILE_LIST_COLUMNS for column in configured_columns)
    ):
        raise ValueError("profile_list.visible_columns contains invalid columns.")
    PROFILE_LIST_VISIBLE_COLUMNS = tuple(
        column for column in DEFAULT_PROFILE_LIST_COLUMNS
        if column in configured_columns
    )

    output = config.get("output", {})
    CUSTOM_LATEST_FOLDER = str(output.get("latest_folder", "")).strip()
    WIDTH = output.get("width", WIDTH)
    configured_height = output.get("height", HEIGHT)
    HEIGHT = None if configured_height in (None, 0) else configured_height
    configured_ratio = output.get("aspect_ratio", ASPECT_RATIO)
    ASPECT_RATIO = configured_ratio or None
    BACKGROUND_COLOR = str(output.get("background_color", BACKGROUND_COLOR))
    configured_render_scale = parse_render_scale_setting(
        output.get(
            "render_scale",
            "auto" if RENDER_SCALE_AUTOMATIC else RENDER_SCALE,
        )
    )
    RENDER_SCALE_AUTOMATIC = configured_render_scale == "auto"
    RENDER_SCALE = (
        1.0 if RENDER_SCALE_AUTOMATIC else float(configured_render_scale)
    )
    if "windows_root" in output:
        OUTPUT_ROOT_WINDOWS = resolve_script_relative_path(output["windows_root"])
    if "linux_root" in output:
        OUTPUT_ROOT_LINUX = resolve_script_relative_path(output["linux_root"])

    view = config.get("view", {})
    PROJECTION = str(view.get("projection", PROJECTION))
    VIEW_PRESET = str(view.get("preset", VIEW_PRESET)).lower()
    configured_bbox = view.get("bbox", CUSTOM_BBOX)
    CUSTOM_BBOX = tuple(configured_bbox) if configured_bbox else None
    VIEW_MODE = str(view.get("fit_mode", VIEW_MODE)).lower()
    ZOOM = view.get("zoom", ZOOM)
    TRUECOLOR_BLACK_NIGHT = bool(
        view.get(
            "truecolor_black_night",
            view.get("truecolour_black_night", TRUECOLOR_BLACK_NIGHT),
        )
    )

    history = config.get("history", {})
    CUSTOM_HISTORY_FOLDER = str(history.get("folder", "")).strip()
    ENABLE_HISTORY = history.get("enabled", ENABLE_HISTORY)
    HISTORY_RETENTION_MODE = str(
        history.get("retention_mode", HISTORY_RETENTION_MODE)
    ).lower()
    HISTORY_MAX_FILES = history.get("max_files", HISTORY_MAX_FILES)
    HISTORY_RETENTION_YEARS = history.get("years", HISTORY_RETENTION_YEARS)
    HISTORY_RETENTION_MONTHS = history.get("months", HISTORY_RETENTION_MONTHS)
    HISTORY_RETENTION_DAYS = history.get("days", HISTORY_RETENTION_DAYS)
    HISTORY_RETENTION_HOURS = history.get("hours", HISTORY_RETENTION_HOURS)
    HISTORY_RETENTION_MINUTES = history.get("minutes", HISTORY_RETENTION_MINUTES)

    windows = config.get("windows", {})
    SET_WINDOWS_WALLPAPER = windows.get(
        "set_wallpaper", SET_WINDOWS_WALLPAPER
    )
    WINDOWS_WALLPAPER_POSITION = str(
        windows.get("position", WINDOWS_WALLPAPER_POSITION)
    ).lower()
    monitor_positions = windows.get("monitor_positions", {})
    if isinstance(monitor_positions, str):
        try:
            monitor_positions = json.loads(monitor_positions)
        except json.JSONDecodeError as exc:
            raise ValueError("windows.monitor_positions is invalid JSON.") from exc
    if not isinstance(monitor_positions, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        or value.lower() not in WALLPAPER_POSITION_CHOICES
        for key, value in monitor_positions.items()
    ):
        raise ValueError("windows.monitor_positions contains invalid monitor settings.")
    WINDOWS_WALLPAPER_MONITOR_POSITIONS = {
        key: value.lower() for key, value in monitor_positions.items()
    }
    WINDOWS_WALLPAPER_MONITOR_OUTPUTS = normalize_monitor_output_settings(
        windows.get("monitor_output_settings", {}),
        {"width": WIDTH, "height": HEIGHT or 0, "aspect_ratio": ASPECT_RATIO,
         "render_scale": get_render_scale_setting(), "background_color": BACKGROUND_COLOR},
    )
    WINDOWS_PAUSE_ON_EXIT = windows.get("pause_on_error", WINDOWS_PAUSE_ON_EXIT)

    updates = config.get("updates", {})
    if not isinstance(updates, dict):
        raise ValueError("TOML 'updates' must be a table.")
    skipped_version = updates.get("skipped_version", "")
    if not isinstance(skipped_version, str):
        raise ValueError("updates.skipped_version must be a string.")
    if skipped_version:
        try:
            version_tuple(skipped_version)
        except ValueError as exc:
            raise ValueError("updates.skipped_version is not a version tag.") from exc
    SKIPPED_UPDATE_VERSION = skipped_version

    if "layers" in config:
        if not isinstance(config["layers"], list):
            raise ValueError("TOML 'layers' must be an array of tables.")
        LAYER_CONFIG = [dict(entry) for entry in config["layers"]]

    refresh_output_paths()
    log(f"Loaded configuration: {config_path.resolve()}")
    return True


def capture_loaded_configuration():
    """Capture live settings so a failed hot reload can be rolled back."""
    state = {}
    for name in LOADED_CONFIGURATION_FIELDS:
        value = globals()[name]
        if name == "LAYER_CONFIG":
            value = [dict(entry) for entry in value]
        elif name in {"SOURCE_PROFILES", "IMAGE_PROFILE_LIBRARY",
                      "WINDOWS_WALLPAPER_MONITOR_POSITIONS",
                      "WINDOWS_WALLPAPER_MONITOR_OUTPUTS"}:
            value = deepcopy(value)
        state[name] = value
    return state


def restore_loaded_configuration(state):
    """Restore a previously captured live configuration."""
    for name in LOADED_CONFIGURATION_FIELDS:
        value = state[name]
        if name == "LAYER_CONFIG":
            value = [dict(entry) for entry in value]
        elif name in {"SOURCE_PROFILES", "IMAGE_PROFILE_LIBRARY",
                      "WINDOWS_WALLPAPER_MONITOR_POSITIONS",
                      "WINDOWS_WALLPAPER_MONITOR_OUTPUTS"}:
            value = deepcopy(value)
        globals()[name] = value
    refresh_output_paths()


# Only settings represented by the Image tab belong to an image profile.
IMAGE_SETTING_FIELDS = {
    "view": {"projection": "PROJECTION", "preset": "VIEW_PRESET", "bbox": "CUSTOM_BBOX",
             "fit_mode": "VIEW_MODE", "zoom": "ZOOM",
             "truecolor_black_night": "TRUECOLOR_BLACK_NIGHT"},
    "output": {"width": "WIDTH", "height": "HEIGHT", "aspect_ratio": "ASPECT_RATIO",
               "background_color": "BACKGROUND_COLOR", "latest_folder": "CUSTOM_LATEST_FOLDER"},
}


def image_settings_snapshot():
    snapshot = {section: {key: deepcopy(globals()[name]) for key, name in fields.items()}
                for section, fields in IMAGE_SETTING_FIELDS.items()}
    snapshot["view"]["bbox"] = list(CUSTOM_BBOX or ())
    snapshot["output"].update(height=HEIGHT or 0, aspect_ratio=ASPECT_RATIO or "",
                              render_scale=get_render_scale_setting())
    snapshot.update(source={"provider": IMAGE_SOURCE,
                            "check_for_updates": CHECK_FOR_SOURCE_UPDATES},
                    sources=deepcopy(SOURCE_PROFILES),
                    layers=deepcopy(LAYER_CONFIG))
    return snapshot


def normalize_image_settings_snapshot(snapshot):
    """Validate a complete Image profile without touching runtime globals or Tk.

    Return an independent copy with normalized known values. Unknown metadata
    in view/output/layer tables is retained. WMS-only validity rules apply to
    EUMETSAT; still-image providers can retain unused WMS settings without changing them.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("Image profile settings must be a table.")
    required = {"source", "sources", "view", "output", "layers"}
    missing = required - snapshot.keys()
    if missing:
        raise ValueError("Image profile is incomplete: missing " + ", ".join(sorted(missing)) + ".")
    for section in required - {"layers"}:
        if not isinstance(snapshot[section], dict):
            raise ValueError(f"Image profile {section} must be a table.")
    if "provider" not in snapshot["source"]:
        raise ValueError("Image profile source.provider is missing.")
    for section, fields in IMAGE_SETTING_FIELDS.items():
        missing = set(fields) - snapshot[section].keys()
        if section == "output" and "render_scale" not in snapshot[section]:
            missing.add("render_scale")
        if missing:
            raise ValueError(f"Image profile {section} is incomplete: " + ", ".join(sorted(missing)) + ".")
    if not isinstance(snapshot["source"]["provider"], str):
        raise ValueError("Image profile source.provider must be text.")
    if type(snapshot["source"].get("check_for_updates", True)) is not bool:
        raise ValueError("Image profile source.check_for_updates must be true or false.")
    result = deepcopy(snapshot)
    provider, profiles = normalize_source_configuration(result["source"]["provider"], result["sources"])
    result["source"]["provider"] = provider
    result["source"]["check_for_updates"] = result["source"].get(
        "check_for_updates", True
    )
    result["sources"] = profiles
    view, output = result["view"], result["output"]

    def number(value, label):
        if type(value) not in (int, float):
            raise ValueError(f"Image profile {label} must be a number.")
        try:
            value = float(value)
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"Image profile {label} must be finite.") from exc
        if not math.isfinite(value):
            raise ValueError(f"Image profile {label} must be finite.")
        return value

    for key in ("projection", "preset", "fit_mode"):
        if not isinstance(view[key], str) or not view[key].strip():
            raise ValueError(f"Image profile view.{key} must be nonempty text.")
    for key in ("truecolor_black_night",):
        if type(view[key]) is not bool:
            raise ValueError(f"Image profile view.{key} must be true or false.")
    if view["fit_mode"] not in {"fit", "crop"}:
        raise ValueError("Image profile fit mode must be fit or crop.")
    view["zoom"] = number(view["zoom"], "zoom")
    if view["zoom"] <= 0 or (provider != "eumetsat" and not 0.05 <= view["zoom"] <= 20):
        raise ValueError("Image profile zoom is outside the supported range.")
    bbox = view["bbox"]
    if not isinstance(bbox, (list, tuple)) or len(bbox) not in (0, 4):
        raise ValueError("Image profile bbox must be empty or contain four numbers.")
    view["bbox"] = [number(value, "bbox") for value in bbox]
    if bbox and not (view["bbox"][0] < view["bbox"][2] and view["bbox"][1] < view["bbox"][3]):
        raise ValueError("Image profile bbox must satisfy xmin < xmax and ymin < ymax.")
    for key in ("width", "height"):
        if type(output[key]) is not int or output[key] < (1 if key == "width" else 0):
            raise ValueError(f"Image profile output.{key} must be a {'positive' if key == 'width' else 'nonnegative'} integer.")
    width, height = output["width"], output["height"]
    aspect = output["aspect_ratio"]
    if aspect is None or aspect == "":
        if height <= 0:
            raise ValueError("Image profile needs a positive height when no aspect ratio is specified.")
        ratio = width / height
        output["aspect_ratio"] = ""
    else:
        if type(aspect) not in (str, int, float):
            raise ValueError("Image profile aspect ratio must be text or a number.")
        ratio = parse_aspect_ratio(aspect)
    try:
        calculated_height = width / ratio
        if not math.isfinite(calculated_height):
            raise ValueError("Image profile calculated height must be finite.")
        actual_height = height or round(calculated_height)
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError("Image profile dimensions are outside the supported range.") from exc
    if actual_height <= 0 or (height and abs(width / height - ratio) / ratio > 0.005):
        raise ValueError("Image profile width and height do not match its aspect ratio.")
    if provider != "eumetsat":
        from marblescape_noaa import MAX_OUTPUT_PIXELS
        if width * actual_height > MAX_OUTPUT_PIXELS or max(width, actual_height) > 32768:
            raise ValueError("Image profile output dimensions are too large for this image source.")
    if type(output["render_scale"]) is bool:
        raise ValueError("Image profile render quality must be auto or a numeric factor.")
    output["render_scale"] = parse_render_scale_setting(output["render_scale"])
    if not isinstance(output["background_color"], str):
        raise ValueError("Image profile background color must be text.")
    output["background_color"] = normalize_background_color(output["background_color"])
    if not isinstance(output["latest_folder"], str) or "\x00" in output["latest_folder"]:
        raise ValueError("Image profile latest folder must be a valid path string.")
    if not isinstance(result["layers"], list) or any(not isinstance(layer, dict) for layer in result["layers"]):
        raise ValueError("Image profile layers must be a list of tables.")
    if provider == "eumetsat":
        if view["projection"] not in PROJECTIONS or view["preset"] not in VIEW_PRESETS:
            raise ValueError("Image profile contains an unknown EUMETSAT projection or preset.")
        if view["preset"] == "custom" and not view["bbox"]:
            raise ValueError("A custom Image profile requires four bbox values.")
        normalized_layers = []
        for index, layer in enumerate(result["layers"], 1):
            if not isinstance(layer.get("name"), str) or not layer["name"].strip():
                raise ValueError(f"Image profile layer {index} needs a name.")
            if "enabled" in layer and type(layer["enabled"]) is not bool:
                raise ValueError(f"Image profile layer {index} enabled must be true or false.")
            if "opacity" in layer:
                number(layer["opacity"], f"layer {index} opacity")
            for key in ("kind", "style", "time"):
                if key in layer and not isinstance(layer[key], str):
                    raise ValueError(f"Image profile layer {index} {key} must be text.")
            normalized_layers.append(normalize_layer_config(layer, index))
        if not any(layer["enabled"] and layer["opacity"] > 0 for layer in normalized_layers):
            raise ValueError("Image profile requires at least one enabled EUMETSAT layer with nonzero opacity.")
        primary_layer = next(
            (layer["name"] for layer in normalized_layers
             if layer["kind"] == "wms" and layer["enabled"] and layer["opacity"] > 0),
            None,
        )
        if primary_layer:
            result["sources"]["eumetsat"]["layer"] = primary_layer
    # Metadata is preserved only when it is valid, bounded profile data too.
    from marblescape_profiles import toml_value
    toml_value(result)
    return result


def apply_image_settings(snapshot):
    """Atomically apply a complete Image snapshot in the runtime worker only."""
    global IMAGE_SOURCE, SOURCE_PROFILES, CHECK_FOR_SOURCE_UPDATES
    global LAYER_CONFIG, RENDER_SCALE, RENDER_SCALE_AUTOMATIC
    snapshot = normalize_image_settings_snapshot(snapshot)
    previous = capture_loaded_configuration()
    try:
        provider, profiles = normalize_source_configuration(
            snapshot["source"]["provider"], snapshot["sources"])
        for section, fields in IMAGE_SETTING_FIELDS.items():
            for key, name in fields.items():
                value = deepcopy(snapshot[section][key])
                if name == "HEIGHT":
                    value = value or None
                elif name == "ASPECT_RATIO":
                    value = value or None
                elif name == "CUSTOM_BBOX":
                    value = tuple(value) if value else None
                globals()[name] = value
        scale = parse_render_scale_setting(snapshot["output"]["render_scale"])
        RENDER_SCALE_AUTOMATIC = scale == "auto"
        RENDER_SCALE = 1.0 if RENDER_SCALE_AUTOMATIC else float(scale)
        IMAGE_SOURCE, SOURCE_PROFILES = provider, profiles
        CHECK_FOR_SOURCE_UPDATES = snapshot["source"].get("check_for_updates", True)
        if not isinstance(snapshot["layers"], list):
            raise ValueError("Image profile layers must be a list.")
        LAYER_CONFIG = deepcopy(snapshot["layers"])
        validate_configuration()
        refresh_output_paths()
    except Exception:
        restore_loaded_configuration(previous)
        raise


def replace_image_settings(text, snapshot):
    """Persist an Image form/profile while retaining General and storage settings."""
    updated = replace_source_configuration(
        text,
        snapshot["source"]["provider"],
        snapshot["sources"],
        snapshot["source"].get("check_for_updates", True),
    )
    for section, fields in IMAGE_SETTING_FIELDS.items():
        keys = (*fields, "render_scale") if section == "output" else fields
        for key in keys:
            value = snapshot[section][key]
            updated = replace_toml_section_value(updated, section, key, value)
    # Preserve every saved layer, including overlays and disabled custom layers.
    from marblescape_profiles import table_spans, toml_value
    for start, end in reversed(table_spans(updated, "layers")):
        updated = updated[:start] + updated[end:]
    first_header = re.search(r"(?m)^[ \t]*\[", updated)
    root_end = first_header.start() if first_header else len(updated)
    root_text = re.sub(r"(?m)^layers\s*=.*(?:\r?\n|$)", "", updated[:root_end])
    updated = root_text + updated[root_end:]
    newline = "\r\n" if "\r\n" in text else "\n"
    updated = updated.rstrip() + newline + newline
    if not snapshot["layers"]:
        updated = "layers = []" + newline + updated
    for layer in snapshot["layers"]:
        updated += "[[layers]]" + newline
        updated += newline.join(
            f'{key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else toml_value(key)} = {toml_value(value)}'
            for key, value in layer.items())
        updated += newline + newline
    tomllib.loads(updated)
    return updated


def set_rotation_status(text, deadline=None, active_profile_id=_ROTATION_ACTIVE_UNCHANGED):
    with ROTATION_STATUS_LOCK:
        ROTATION_STATUS.update(text=text, deadline=deadline)
        if active_profile_id is not _ROTATION_ACTIVE_UNCHANGED:
            ROTATION_STATUS["active_profile_id"] = active_profile_id


# =============================================================================
# CONFIGURATION VALIDATION
# =============================================================================


def parse_aspect_ratio(value):
    if value is None:
        if WIDTH is None or HEIGHT is None:
            raise ValueError(
                "WIDTH and HEIGHT must both be set when ASPECT_RATIO is None."
            )
        if WIDTH <= 0 or HEIGHT <= 0:
            raise ValueError("WIDTH and HEIGHT must be greater than zero.")
        return WIDTH / HEIGHT

    if isinstance(value, (int, float)):
        ratio = float(value)
        if not math.isfinite(ratio) or ratio <= 0:
            raise ValueError("ASPECT_RATIO must be greater than zero.")
        return ratio

    value = str(value).strip()
    separator = ":" if ":" in value else "/" if "/" in value else None

    if separator is None:
        try:
            ratio = float(value)
        except ValueError as exc:
            raise ValueError(f"Invalid ASPECT_RATIO: {value}") from exc
        if not math.isfinite(ratio) or ratio <= 0:
            raise ValueError("ASPECT_RATIO must be greater than zero.")
        return ratio

    left, right = value.split(separator, 1)
    try:
        width_ratio = float(left.strip())
        height_ratio = float(right.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid ASPECT_RATIO: {value}") from exc

    if (
        not math.isfinite(width_ratio)
        or not math.isfinite(height_ratio)
        or width_ratio <= 0
        or height_ratio <= 0
    ):
        raise ValueError("ASPECT_RATIO values must be greater than zero.")

    ratio = width_ratio / height_ratio
    if not math.isfinite(ratio) or ratio <= 0:
        raise ValueError("ASPECT_RATIO must resolve to a finite value.")
    return ratio


def normalize_aspect_ratio_text(value):
    """Return a validated, compact aspect-ratio value for the TOML file."""
    parse_aspect_ratio(value)
    text = str(value).strip()
    separator = ":" if ":" in text else "/" if "/" in text else None

    if separator is None:
        return format(float(text), ".12g")

    left, right = text.split(separator, 1)

    def format_component(component):
        number = float(component.strip())
        return str(int(number)) if number.is_integer() else format(number, ".12g")

    return f"{format_component(left)}:{format_component(right)}"


def aspect_ratio_for_dimensions(width, height):
    """Return the exact reduced aspect ratio for positive pixel dimensions."""
    if width <= 0 or height <= 0:
        raise ValueError("Width and height must be greater than zero.")
    divisor = math.gcd(int(width), int(height))
    return f"{int(width) // divisor}:{int(height) // divisor}"


def parse_resolution_text(value, automatic_aspect_ratio):
    """Parse WIDTH x HEIGHT text and return matching TOML output values."""
    match = re.fullmatch(r"\s*(\d+)\s*[xX\u00d7]\s*(\d+)\s*", str(value))
    if match is None:
        raise ValueError("Resolution must use the format WIDTH x HEIGHT.")

    width = int(match.group(1))
    height = int(match.group(2))
    if width <= 0:
        raise ValueError("Width must be greater than zero.")

    if height == 0:
        if automatic_aspect_ratio is None:
            raise ValueError(
                "An aspect ratio is required when the custom height is zero."
            )
        aspect_ratio = normalize_aspect_ratio_text(automatic_aspect_ratio)
    else:
        aspect_ratio = aspect_ratio_for_dimensions(width, height)

    return width, height, aspect_ratio


def get_output_dimensions():
    ratio = parse_aspect_ratio(ASPECT_RATIO)

    if WIDTH is None or WIDTH <= 0:
        raise ValueError("WIDTH must be greater than zero.")

    if HEIGHT is None:
        calculated_height = round(WIDTH / ratio)
        if calculated_height <= 0:
            raise ValueError("The calculated output height is invalid.")
        return int(WIDTH), int(calculated_height)

    if HEIGHT <= 0:
        raise ValueError("HEIGHT must be greater than zero.")

    actual_ratio = WIDTH / HEIGHT
    relative_difference = abs(actual_ratio - ratio) / ratio
    if relative_difference > 0.005:
        raise ValueError(
            "WIDTH and HEIGHT do not match ASPECT_RATIO. "
            "Set HEIGHT to None or use matching dimensions."
        )

    return int(WIDTH), int(HEIGHT)


MONITOR_OUTPUT_FIELDS = (
    "width", "height", "aspect_ratio", "render_scale", "background_color",
)


def normalize_monitor_output_settings(value, defaults):
    """Validate saved per-display overrides without requiring attached displays."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("windows.monitor_output_settings is invalid JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("windows.monitor_output_settings must be a monitor map.")
    normalized = {}
    for monitor_id, fields in value.items():
        if not isinstance(monitor_id, str) or not monitor_id or not isinstance(fields, dict):
            raise ValueError("Monitor output settings need a device ID and field map.")
        if set(fields) - set(MONITOR_OUTPUT_FIELDS):
            raise ValueError("Monitor output settings contain an unknown field.")
        settings = dict(defaults)
        settings.update(fields)
        try:
            width = int(settings["width"])
            height = int(settings["height"])
            ratio = normalize_aspect_ratio_text(settings["aspect_ratio"])
            scale = parse_render_scale_setting(settings["render_scale"])
            color = normalize_background_color(settings["background_color"])
            actual_height = height or round(width / parse_aspect_ratio(ratio))
        except (TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
            raise ValueError(f"Invalid output settings for monitor {monitor_id}.") from exc
        if (width <= 0 or height < 0 or actual_height <= 0
                or max(width, actual_height) > 32768
                or width * actual_height > 33_554_432
                or (height and abs(width / height - parse_aspect_ratio(ratio))
                    / parse_aspect_ratio(ratio) > 0.005)):
            raise ValueError(f"Invalid output dimensions for monitor {monitor_id}.")
        typed = {"width": width, "height": height, "aspect_ratio": ratio,
                 "render_scale": scale, "background_color": color}
        normalized[monitor_id] = {key: typed[key] for key in fields}
    return normalized


def get_render_dimensions(output_width, output_height):
    """Return capped WMS dimensions and the effective supersampling factor."""
    maximum_scale = min(
        MAX_WMS_DIMENSION / output_width,
        MAX_WMS_DIMENSION / output_height,
    )
    requested_scale = get_requested_render_scale(output_width, output_height)
    effective_scale = min(requested_scale, maximum_scale)
    render_width = min(
        MAX_WMS_DIMENSION,
        max(1, round(output_width * effective_scale)),
    )
    render_height = min(
        MAX_WMS_DIMENSION,
        max(1, round(output_height * effective_scale)),
    )
    return int(render_width), int(render_height), float(effective_scale)


def get_render_scale_setting():
    """Return the serializable render-quality setting."""
    return "auto" if RENDER_SCALE_AUTOMATIC else RENDER_SCALE


def get_requested_render_scale(output_width, output_height):
    """Return the requested factor for the current output dimensions."""
    if RENDER_SCALE_AUTOMATIC:
        return min(
            MAX_WMS_DIMENSION / output_width,
            MAX_WMS_DIMENSION / output_height,
        )
    return RENDER_SCALE


def normalize_layer_config(entry, index):
    if not isinstance(entry, dict):
        raise ValueError(f"Layer {index} must be a TOML table.")

    name = str(entry.get("name", "")).strip()
    if not name:
        raise ValueError(f"Layer {index} has no name.")

    kind = str(entry.get("kind", "wms")).lower()
    if kind not in {"wms", "basemap", "overlay"}:
        raise ValueError(
            f"Layer {index} ({name}) has unsupported kind '{kind}'."
        )

    try:
        opacity = float(entry.get("opacity", 1.0))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Layer {index} ({name}) has invalid opacity.") from exc
    if not 0.0 <= opacity <= 1.0:
        raise ValueError(f"Layer {index} ({name}) opacity must be from 0.0 to 1.0.")

    time_specified = "time" in entry
    layer_time = entry.get("time")
    if layer_time is not None:
        layer_time = str(layer_time).strip()
        if not layer_time or layer_time.lower() == "latest":
            layer_time = None

    return {
        "kind": kind,
        "name": name,
        "enabled": bool(entry.get("enabled", True)),
        "opacity": opacity,
        "style": str(entry.get("style", "")).strip(),
        "time": layer_time,
        "time_specified": time_specified,
    }


def get_configured_layers():
    return [
        normalize_layer_config(entry, index)
        for index, entry in enumerate(LAYER_CONFIG, start=1)
    ]


def parse_background_color(value):
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    elif text.startswith("#"):
        text = text[1:]
    if len(text) != 6:
        raise ValueError("BACKGROUND_COLOR must contain exactly six RGB hex digits.")
    try:
        return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError as exc:
        raise ValueError("BACKGROUND_COLOR is not a valid RGB hex color.") from exc


def normalize_background_color(value):
    red, green, blue = parse_background_color(value)
    return f"#{red:02X}{green:02X}{blue:02X}"


def normalize_source_configuration(provider, profiles):
    """Validate saved source selections without requiring a network connection."""
    if provider not in SOURCE_LABELS:
        raise ValueError(f"Unknown image source: {provider}")
    if not isinstance(profiles, dict):
        raise ValueError("[sources] must contain source selection tables.")
    normalized = {}
    for key, defaults in DEFAULT_SOURCE_PROFILES.items():
        profile = profiles.get(key, {})
        if not isinstance(profile, dict):
            raise ValueError(f"[sources.{key}] must be a table.")
        if key == "eumetsat":
            normalized[key] = normalize_eumetsat_profile(profile)
            continue
        if key == "copernicus":
            normalized[key] = normalize_copernicus_profile(profile)
            continue
        normalized[key] = dict(defaults)
        for field in defaults:
            value = profile.get(field, defaults[field])
            if not isinstance(value, str) or not value.strip() or len(value) > 300:
                raise ValueError(f"Invalid {field} for {SOURCE_LABELS[key]}.")
            normalized[key][field] = value.strip()
        if normalized[key]["resolution"] not in {"auto", "largest"} and not re.fullmatch(
            r"[1-9]\d{1,4}x[1-9]\d{1,4}", normalized[key]["resolution"]
        ):
            raise ValueError(f"Invalid source resolution for {SOURCE_LABELS[key]}.")
    return provider, normalized


def validate_wms_configuration():
    """Validate only the settings used by the EUMETSAT provider."""
    configured_layers = get_configured_layers()
    if not any(layer["enabled"] and layer["opacity"] > 0 for layer in configured_layers):
        raise ValueError("At least one enabled layer with opacity above zero is required.")

    if RENDER_MODE not in {"auto", "server", "local"}:
        raise ValueError("RENDER_MODE must be 'auto', 'server', or 'local'.")

    if RENDER_MODE == "server" and any(
        layer["enabled"] and layer["opacity"] != 1.0
        for layer in configured_layers
    ):
        raise ValueError(
            "Per-layer opacity requires render_mode='auto' or 'local'."
        )

    if PROJECTION not in PROJECTIONS:
        raise ValueError(f"Unsupported PROJECTION: {PROJECTION}")

    if VIEW_PRESET not in VIEW_PRESETS:
        raise ValueError(
            f"Unsupported VIEW_PRESET: {VIEW_PRESET}. "
            f"Use one of: {', '.join(VIEW_PRESETS)}"
        )

    if VIEW_PRESET == "custom":
        if CUSTOM_BBOX is None or len(CUSTOM_BBOX) != 4:
            raise ValueError("The custom view requires four bbox values.")
        try:
            xmin, ymin, xmax, ymax = map(float, CUSTOM_BBOX)
        except (TypeError, ValueError) as exc:
            raise ValueError("The custom bbox contains a non-numeric value.") from exc
        if xmin >= xmax or ymin >= ymax:
            raise ValueError("The custom bbox must satisfy xmin < xmax and ymin < ymax.")


def validate_configuration():
    normalize_source_configuration(IMAGE_SOURCE, SOURCE_PROFILES)
    normalize_time_zone(DISPLAY_TIME_ZONE)
    if IMAGE_SOURCE == "eumetsat":
        validate_wms_configuration()
    elif IMAGE_SOURCE == "copernicus" and not (
        COPERNICUS_CLIENT_ID and COPERNICUS_CLIENT_SECRET
    ):
        raise ValueError(
            "Copernicus requires a Sentinel Hub OAuth Client ID and Client secret in Settings > Image."
        )

    if VIEW_MODE not in {"fit", "crop"}:
        raise ValueError("VIEW_MODE must be 'fit' or 'crop'.")

    if not math.isfinite(ZOOM) or ZOOM <= 0:
        raise ValueError("ZOOM must be greater than zero.")

    if (
        not RENDER_SCALE_AUTOMATIC
        and (not math.isfinite(RENDER_SCALE) or RENDER_SCALE < 1.0)
    ):
        raise ValueError("RENDER_SCALE must be a finite value of at least 1.0.")

    if not math.isfinite(UPDATE_INTERVAL_MINUTES) or UPDATE_INTERVAL_MINUTES <= 0:
        raise ValueError("UPDATE_INTERVAL_MINUTES must be greater than zero.")

    if not math.isfinite(NETWORK_TIMEOUT_SECONDS) or NETWORK_TIMEOUT_SECONDS <= 0:
        raise ValueError("NETWORK_TIMEOUT_SECONDS must be greater than zero.")

    parse_background_color(BACKGROUND_COLOR)

    if HISTORY_RETENTION_MODE not in {"count", "time", "both"}:
        raise ValueError(
            "HISTORY_RETENTION_MODE must be 'count', 'time', or 'both'."
        )

    if HISTORY_RETENTION_MODE in {"count", "both"} and HISTORY_MAX_FILES < 0:
        raise ValueError("HISTORY_MAX_FILES cannot be negative.")

    retention_values = (
        HISTORY_RETENTION_YEARS,
        HISTORY_RETENTION_MONTHS,
        HISTORY_RETENTION_DAYS,
        HISTORY_RETENTION_HOURS,
        HISTORY_RETENTION_MINUTES,
    )

    if any(value < 0 for value in retention_values):
        raise ValueError("History retention time values cannot be negative.")

    if HISTORY_RETENTION_MODE in {"time", "both"} and not any(retention_values):
        raise ValueError(
            "At least one time-based history retention value must be greater than zero."
        )

    if WINDOWS_WALLPAPER_POSITION.lower() not in WALLPAPER_POSITION_CHOICES:
        raise ValueError(
            f"Unsupported WINDOWS_WALLPAPER_POSITION: {WINDOWS_WALLPAPER_POSITION}"
        )
    if any(value not in WALLPAPER_POSITION_CHOICES
           for value in WINDOWS_WALLPAPER_MONITOR_POSITIONS.values()):
        raise ValueError("Unsupported monitor wallpaper position.")

    get_output_dimensions()


# =============================================================================
# MAP EXTENT AND OUTPUT SIZE
# =============================================================================


def get_active_view():
    preset = VIEW_PRESETS[VIEW_PRESET]
    projection_name = preset["projection"] or PROJECTION
    projection = PROJECTIONS[projection_name]

    if VIEW_PRESET == "custom":
        extent = tuple(map(float, CUSTOM_BBOX))
    elif preset["bbox"] is not None:
        extent = tuple(map(float, preset["bbox"]))
    else:
        extent = (
            projection["xmin"],
            projection["ymin"],
            projection["xmax"],
            projection["ymax"],
        )

    return projection_name, projection, extent


def serialize_bbox(projection, extent):
    xmin, ymin, xmax, ymax = extent
    if projection["axis_order"] == "yx":
        values = (ymin, xmin, ymax, xmax)
    else:
        values = (xmin, ymin, xmax, ymax)
    return ",".join(f"{value:.6f}" for value in values)


def calculate_bbox(projection, target_ratio, base_extent):
    xmin, ymin, xmax, ymax = base_extent

    full_width = xmax - xmin
    full_height = ymax - ymin
    base_ratio = full_width / full_height

    center_x = (xmin + xmax) / 2.0
    center_y = (ymin + ymax) / 2.0

    if VIEW_MODE == "fit":
        if target_ratio > base_ratio:
            new_height = full_height
            new_width = full_height * target_ratio
        else:
            new_width = full_width
            new_height = full_width / target_ratio
    else:
        if target_ratio > base_ratio:
            new_width = full_width
            new_height = full_width / target_ratio
        else:
            new_height = full_height
            new_width = full_height * target_ratio

    new_width /= ZOOM
    new_height /= ZOOM

    new_xmin = center_x - new_width / 2.0
    new_xmax = center_x + new_width / 2.0
    new_ymin = center_y - new_height / 2.0
    new_ymax = center_y + new_height / 2.0

    if projection["crs"] == "EPSG:4326" and (
        new_xmin < -180.0
        or new_xmax > 180.0
        or new_ymin < -90.0
        or new_ymax > 90.0
    ):
        raise ValueError(
            "The calculated geographic bbox exceeds valid longitude/latitude "
            "bounds. Use fit_mode='crop', a larger zoom, or a regional preset."
        )

    extent = (new_xmin, new_ymin, new_xmax, new_ymax)
    return serialize_bbox(projection, extent), extent


# =============================================================================
# WMS CAPABILITIES AND LAYER RESOLUTION
# =============================================================================


def make_request(url):
    return Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


def download_capabilities():
    params = {
        "service": "WMS",
        "version": WMS_VERSION,
        "request": "GetCapabilities",
    }
    url = WMS_URL + "?" + urlencode(params)

    log("Downloading WMS capabilities...")
    with urlopen(make_request(url), timeout=NETWORK_TIMEOUT_SECONDS) as response:
        return response.read()


def parse_layers(xml_data):
    root = ET.fromstring(xml_data)
    result = []

    def direct_text(element, tag_name):
        for child in element:
            if local_xml_name(child.tag) == tag_name and child.text:
                return child.text.strip()
        return ""

    def parse_layer(layer, inherited):
        crs_values = set(inherited["crs"])
        styles = dict(inherited["styles"])
        dimensions = dict(inherited["dimensions"])
        bounding_boxes = dict(inherited["bounding_boxes"])
        geographic_bbox = inherited["geographic_bbox"]

        for child in layer:
            tag = local_xml_name(child.tag)

            if tag in {"CRS", "SRS"} and child.text:
                crs_values.update(child.text.split())
            elif tag == "Style":
                style_name = direct_text(child, "Name")
                if style_name:
                    styles[style_name] = {
                        "name": style_name,
                        "title": direct_text(child, "Title"),
                        "abstract": direct_text(child, "Abstract"),
                    }
            elif tag in {"Dimension", "Extent"}:
                dimension_name = child.attrib.get("name", "").strip()
                if dimension_name:
                    dimensions[dimension_name] = {
                        "name": dimension_name,
                        "units": child.attrib.get("units", ""),
                        "default": child.attrib.get("default", ""),
                        "nearest_value": child.attrib.get("nearestValue", ""),
                        "multiple_values": child.attrib.get("multipleValues", ""),
                        "current": child.attrib.get("current", ""),
                        "values": (child.text or "").strip(),
                    }
            elif tag == "BoundingBox":
                bbox_crs = child.attrib.get("CRS") or child.attrib.get("SRS")
                if bbox_crs:
                    try:
                        bounding_boxes[bbox_crs] = [
                            float(child.attrib[key])
                            for key in ("minx", "miny", "maxx", "maxy")
                        ]
                    except (KeyError, ValueError):
                        pass
            elif tag in {"EX_GeographicBoundingBox", "LatLonBoundingBox"}:
                if tag == "LatLonBoundingBox":
                    try:
                        geographic_bbox = [
                            float(child.attrib[key])
                            for key in ("minx", "miny", "maxx", "maxy")
                        ]
                    except (KeyError, ValueError):
                        pass
                else:
                    values = {}
                    for coordinate in child:
                        if coordinate.text:
                            try:
                                values[local_xml_name(coordinate.tag)] = float(
                                    coordinate.text
                                )
                            except ValueError:
                                pass
                    required = (
                        "westBoundLongitude",
                        "southBoundLatitude",
                        "eastBoundLongitude",
                        "northBoundLatitude",
                    )
                    if all(key in values for key in required):
                        geographic_bbox = [values[key] for key in required]

        opaque = inherited["opaque"]
        if "opaque" in layer.attrib:
            opaque = layer.attrib["opaque"] not in {"0", "false", "False"}

        queryable = inherited["queryable"]
        if "queryable" in layer.attrib:
            queryable = layer.attrib["queryable"] not in {"0", "false", "False"}

        current = {
            "crs": crs_values,
            "styles": styles,
            "dimensions": dimensions,
            "bounding_boxes": bounding_boxes,
            "geographic_bbox": geographic_bbox,
            "opaque": opaque,
            "queryable": queryable,
        }

        name = direct_text(layer, "Name")
        if name:
            result.append(
                {
                    "name": name,
                    "title": direct_text(layer, "Title"),
                    "abstract": direct_text(layer, "Abstract"),
                    "crs": sorted(crs_values),
                    "styles": sorted(styles.values(), key=lambda item: item["name"]),
                    "dimensions": dimensions,
                    "bounding_boxes": bounding_boxes,
                    "geographic_bbox": geographic_bbox,
                    "opaque": opaque,
                    "queryable": queryable,
                }
            )

        for child in layer:
            if local_xml_name(child.tag) == "Layer":
                parse_layer(child, current)

    empty = {
        "crs": set(),
        "styles": {},
        "dimensions": {},
        "bounding_boxes": {},
        "geographic_bbox": None,
        "opaque": False,
        "queryable": False,
    }
    top_layers = []
    for element in root.iter():
        if local_xml_name(element.tag) == "Capability":
            top_layers = [
                child for child in element if local_xml_name(child.tag) == "Layer"
            ]
            break

    if not top_layers:
        top_layers = [
            element for element in root if local_xml_name(element.tag) == "Layer"
        ]

    for layer in top_layers:
        parse_layer(layer, empty)

    return result


def layer_exists(layers, name):
    return any(layer["name"] == name for layer in layers)


def get_layer_metadata(layers, name):
    return next((layer for layer in layers if layer["name"] == name), None)


def filter_layer_catalog(layers, search_text=""):
    words = str(search_text or "").lower().split()
    if not words:
        return list(layers)
    return [
        layer
        for layer in layers
        if all(
            word in f"{layer['name']} {layer['title']} {layer['abstract']}".lower()
            for word in words
        )
    ]


def print_layer_catalog(layers, search_text=""):
    matches = filter_layer_catalog(layers, search_text)
    print(f"Available WMS layers: {len(matches)} of {len(layers)}")
    for layer in matches:
        style_names = ", ".join(style["name"] for style in layer["styles"])
        time_dimension = layer["dimensions"].get("time")
        details = []
        if style_names:
            details.append(f"styles={style_names}")
        if time_dimension:
            details.append("time=yes")
        suffix = f" [{'; '.join(details)}]" if details else ""
        print(f"{layer['name']} | {layer['title']}{suffix}")


def export_layer_catalog(layers, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(layers, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"Exported {len(layers)} WMS layers to: {output_path.resolve()}")


def find_layer(layers, required_words, excluded_words=(), prefer_workspace=None):
    candidates = []

    for layer in layers:
        searchable = f"{layer['name']} {layer['title']}".lower()

        if not all(word.lower() in searchable for word in required_words):
            continue
        if any(word.lower() in searchable for word in excluded_words):
            continue

        score = 0
        if prefer_workspace and layer["name"].startswith(prefer_workspace + ":"):
            score += 100
        score -= len(layer["name"])
        candidates.append((score, layer["name"], layer["title"]))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]


def resolve_overlay(name, layers):
    if name in KNOWN_OVERLAYS:
        exact = KNOWN_OVERLAYS[name]
        if layer_exists(layers, exact):
            return exact

    if name == "Coastlines":
        return find_layer(
            layers,
            required_words=("coast",),
            prefer_workspace="backgrounds",
        )

    if name == "Boundaries":
        preferred = find_layer(
            layers,
            required_words=("gisco", "bound"),
            excluded_words=("label",),
            prefer_workspace="backgrounds",
        )
        if preferred:
            return preferred

        fallback = "backgrounds:ne_boundary_lines_land"
        if layer_exists(layers, fallback):
            return fallback

        return find_layer(
            layers,
            required_words=("bound",),
            excluded_words=("label",),
            prefer_workspace="backgrounds",
        )

    if name == "Labels (dark)":
        return find_layer(
            layers,
            required_words=("dark", "label"),
            prefer_workspace="osmgray",
        )

    if name == "Labels (light)":
        return find_layer(
            layers,
            required_words=("light", "label"),
            prefer_workspace="osmgray",
        )

    if name == "Graticules (dark)":
        return find_layer(
            layers,
            required_words=("gratic", "dark"),
        )

    if name == "Graticules (light)":
        return find_layer(
            layers,
            required_words=("gratic", "light"),
        )

    raise ValueError(f"Unsupported overlay: {name}")


def resolve_basemap(name, layers):
    if name is None:
        return None

    if name == "Natural Earth":
        for candidate in ("backgrounds:ne_gray", "osmgray:ne_gray"):
            if layer_exists(layers, candidate):
                return candidate

        return find_layer(
            layers,
            required_words=("natural", "earth"),
            excluded_words=("coast", "boundary", "label", "gratic"),
        )

    if name == "OSM Dark":
        return find_layer(
            layers,
            required_words=("dark",),
            excluded_words=("label", "gratic", "boundary", "coast"),
            prefer_workspace="osmgray",
        )

    if name == "OSM Light":
        for candidate in ("osmgray:light", "osmgray:light_bg"):
            if layer_exists(layers, candidate):
                return candidate

        return find_layer(
            layers,
            required_words=("light",),
            excluded_words=("label", "gratic", "boundary", "coast"),
            prefer_workspace="osmgray",
        )

    raise ValueError(f"Unsupported basemap: {name}")


def get_latest_layer_time(metadata):
    """Return the newest explicit timestamp advertised for a WMS layer."""
    time_dimension = metadata.get("dimensions", {}).get("time")
    if not time_dimension:
        return None

    default = str(time_dimension.get("default", "")).strip()
    if default and default.lower() not in {"current", "latest"}:
        return default

    values = str(time_dimension.get("values", "")).strip()
    if not values:
        return None

    newest = values.split(",")[-1].strip()
    interval_parts = newest.split("/")
    if len(interval_parts) >= 2:
        newest = interval_parts[1].strip()
    return newest or None


_WMS_DURATION_PATTERN = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


def parse_wms_duration(value):
    """Parse the fixed ISO-8601 durations used by EUMETSAT time dimensions."""
    match = _WMS_DURATION_PATTERN.fullmatch(str(value).strip().upper())
    if not match:
        return None
    parts = match.groupdict(default="0")
    duration = dt.timedelta(
        days=int(parts["days"]),
        hours=int(parts["hours"]),
        minutes=int(parts["minutes"]),
        seconds=float(parts["seconds"]),
    )
    return duration if duration.total_seconds() > 0 else None


def parse_wms_datetime(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def format_wms_datetime(value):
    value = value.astimezone(dt.timezone.utc).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")


def get_gap_fill_layer_times(metadata, current_time, lookback_hours, maximum=32):
    """Return older WMS times in chronological order for an LEO mosaic."""
    current = parse_wms_datetime(current_time)
    if current is None:
        return []
    cutoff = current - dt.timedelta(hours=int(lookback_hours))
    values = str(
        metadata.get("dimensions", {}).get("time", {}).get("values", "")
    ).strip()
    explicit = []
    interval_steps = []
    for entry in (values.split(",") if values else ()):
        parts = [part.strip() for part in entry.split("/")]
        if len(parts) == 1:
            parsed = parse_wms_datetime(parts[0])
            if parsed is not None:
                explicit.append(parsed)
        elif len(parts) >= 3:
            step = parse_wms_duration(parts[2])
            if step is not None:
                interval_steps.append(step)
    if explicit:
        selected = sorted({value for value in explicit if cutoff <= value < current})
        return [format_wms_datetime(value) for value in selected[-maximum:]]
    if not interval_steps:
        return []
    step = min(interval_steps)
    selected = []
    value = current - step
    while value >= cutoff and len(selected) < maximum:
        selected.append(value)
        value -= step
    return [format_wms_datetime(value) for value in reversed(selected)]


def eumetsat_gap_fill_profile():
    profile = SOURCE_PROFILES.get("eumetsat", {})
    if not profile.get("fill_gaps"):
        return None
    if not eumetsat_supports_gap_fill(
        profile.get("layer", ""), profile.get("orbit_type", "")
    ):
        return None
    return profile


def resolve_configured_layers(layers, projection, log_resolutions=True):
    resolved_layers = []

    for configured in get_configured_layers():
        if not configured["enabled"] or configured["opacity"] == 0:
            continue

        if configured["kind"] == "wms":
            layer_name = configured["name"]
        elif configured["kind"] == "basemap":
            layer_name = resolve_basemap(configured["name"], layers)
        else:
            layer_name = resolve_overlay(configured["name"], layers)

        if not layer_name or not layer_exists(layers, layer_name):
            raise RuntimeError(
                f"Configured {configured['kind']} layer is unavailable: "
                f"{configured['name']}"
            )

        metadata = get_layer_metadata(layers, layer_name)
        available_styles = {style["name"] for style in metadata["styles"]}
        if configured["style"] and configured["style"] not in available_styles:
            raise RuntimeError(
                f"Style '{configured['style']}' is unavailable for {layer_name}. "
                f"Available: {', '.join(sorted(available_styles)) or 'default only'}"
            )

        # AUTO geostationary projections are GeoServer-defined on demand and
        # therefore are not necessarily listed per layer in GetCapabilities.
        if (
            not projection["crs"].startswith("AUTO:")
            and metadata["crs"]
            and projection["crs"] not in metadata["crs"]
        ):
            raise RuntimeError(
                f"Layer {layer_name} does not advertise CRS {projection['crs']}."
            )

        uses_latest_time = (
            configured["time_specified"] and configured["time"] is None
        ) or (
            not configured["time_specified"] and IMAGE_TIME is None
        )
        if uses_latest_time:
            effective_time = get_latest_layer_time(metadata)
            if "time" in metadata["dimensions"] and not effective_time:
                raise RuntimeError(
                    f"No explicit latest timestamp is advertised for {layer_name}."
                )
        else:
            effective_time = (
                configured["time"] if configured["time_specified"] else IMAGE_TIME
            )
        resolved = dict(configured)
        resolved.update(
            {
                "requested_name": configured["name"],
                "name": layer_name,
                "title": metadata["title"],
                "time": effective_time,
                "uses_latest_time": uses_latest_time and effective_time is not None,
                "metadata": metadata,
            }
        )
        resolved_layers.append(resolved)

        if log_resolutions and configured["name"] != layer_name:
            log(
                f"Resolved {configured['kind']}: "
                f"{configured['name']} -> {layer_name}"
            )

    return resolved_layers


def normalize_wms_background_color():
    red, green, blue = parse_background_color(BACKGROUND_COLOR)
    return f"0x{red:02X}{green:02X}{blue:02X}"


def build_getmap_url(
    layer_names,
    styles,
    projection,
    bbox,
    output_width,
    output_height,
    *,
    transparent,
    image_time,
):
    params = {
        "service": "WMS",
        "version": WMS_VERSION,
        "request": "GetMap",
        "layers": ",".join(layer_names),
        "styles": ",".join(styles),
        "crs": projection["crs"],
        "bbox": bbox,
        "width": output_width,
        "height": output_height,
        "format": IMAGE_FORMAT,
        "transparent": "true" if transparent else "false",
        "bgcolor": normalize_wms_background_color(),
    }

    if image_time:
        params["time"] = image_time

    return WMS_URL + "?" + urlencode(params, safe=",:")


def determine_render_mode(resolved_layers):
    if eumetsat_gap_fill_profile() is not None:
        return "local"
    if RENDER_MODE in {"server", "local"}:
        return RENDER_MODE

    opacities_require_local = any(
        layer["opacity"] != 1.0 for layer in resolved_layers
    )
    layer_times = {
        layer["time"] for layer in resolved_layers if layer["time"] is not None
    }
    times_require_local = len(layer_times) > 1
    return "local" if opacities_require_local or times_require_local else "server"


def should_blacken_truecolor_night(resolved_layers):
    if not TRUECOLOR_BLACK_NIGHT:
        return False

    primary_wms_layer = next(
        (layer for layer in resolved_layers if layer["kind"] == "wms"),
        None,
    )
    return (
        primary_wms_layer is not None
        and primary_wms_layer["name"] == TRUECOLOR_LAYER_NAME
    )


def build_render_plan(
    resolved_layers,
    projection,
    bbox,
    output_width,
    output_height,
):
    if should_blacken_truecolor_night(resolved_layers):
        requests = [
            {
                "url": build_getmap_url(
                    [TRUECOLOR_EARTH_MASK_LAYER],
                    [""],
                    projection,
                    bbox,
                    output_width,
                    output_height,
                    transparent=True,
                    image_time=None,
                ),
                "label": f"{TRUECOLOR_EARTH_MASK_LAYER} (Earth mask)",
                "opacity": 1.0,
                "role": "earth_mask",
            }
        ]

        for layer in resolved_layers:
            if layer["kind"] == "basemap":
                continue
            requests.append(
                {
                    "url": build_getmap_url(
                        [layer["name"]],
                        [layer["style"]],
                        projection,
                        bbox,
                        output_width,
                        output_height,
                        transparent=True,
                        image_time=layer["time"],
                    ),
                    "label": layer["name"],
                    "opacity": layer["opacity"],
                    "role": "content",
                }
            )

        return "truecolor_black_night", requests

    render_mode = determine_render_mode(resolved_layers)

    if render_mode == "server":
        if any(layer["opacity"] != 1.0 for layer in resolved_layers):
            raise ValueError("Server rendering does not support per-layer opacity.")
        layer_times = {
            layer["time"] for layer in resolved_layers if layer["time"] is not None
        }
        if len(layer_times) > 1:
            raise ValueError("Server rendering cannot use different per-layer times.")
        image_time = next(iter(layer_times), None)
        if VIEW_PRESET == "full_earth":
            # In the geostationary full-Earth projection, the Natural Earth
            # basemap is opaque. Keep configured bottom-to-top order so the
            # selected satellite product is rendered above the basemap.
            server_layers = list(resolved_layers)
        else:
            # Preserve the established rendering behavior of regional presets.
            server_layers = list(reversed(resolved_layers))
        url = build_getmap_url(
            [layer["name"] for layer in server_layers],
            [layer["style"] for layer in server_layers],
            projection,
            bbox,
            output_width,
            output_height,
            transparent=False,
            image_time=image_time,
        )
        return render_mode, [
            {
                "url": url,
                "label": ", ".join(layer["name"] for layer in resolved_layers),
                "opacity": 1.0,
            }
        ]

    requests = []
    gap_fill = eumetsat_gap_fill_profile()
    gap_fill_applied = False
    for layer in resolved_layers:
        image_times = [layer["time"]]
        is_gap_fill_target = (
            gap_fill is not None
            and layer["kind"] == "wms"
            and layer["name"] == gap_fill["layer"]
        )
        if is_gap_fill_target:
            gap_fill_applied = True
            if not layer["time"] or "time" not in layer["metadata"]["dimensions"]:
                raise RuntimeError(
                    "The selected EUMETSAT layer does not advertise archive times "
                    "required for gap filling."
                )
            image_times = [
                *get_gap_fill_layer_times(
                    layer["metadata"], layer["time"],
                    gap_fill["gap_fill_lookback_hours"],
                ),
                layer["time"],
            ]
        for image_time in image_times:
            label = layer["name"]
            if is_gap_fill_target:
                label += f" @ {image_time}"
            requests.append(
                {
                    "url": build_getmap_url(
                        [layer["name"]],
                        [layer["style"]],
                        projection,
                        bbox,
                        output_width,
                        output_height,
                        transparent=True,
                        image_time=image_time,
                    ),
                    "label": label,
                    "opacity": layer["opacity"],
                    "role": "gap_fill" if is_gap_fill_target else "content",
                }
            )
    if gap_fill is not None and not gap_fill_applied:
        raise RuntimeError(
            "EUMETSAT gap filling is enabled, but the selected single-overpass "
            "layer is not an enabled WMS layer."
        )
    return render_mode, requests


# =============================================================================
# IMAGE DOWNLOAD AND INSTALLATION
# =============================================================================


def download_image(url, label=None):
    if label:
        log(f"Requesting layer: {label}")
    else:
        log("Requesting the latest image...")

    DOWNLOAD_PROGRESS.raise_if_cancelled()
    try:
        with urlopen(make_request(url), timeout=NETWORK_TIMEOUT_SECONDS) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            data = read_response(response, track=True)
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {error_body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc

    if not content_type.startswith("image/"):
        error_text = data.decode("utf-8", errors="replace")
        raise RuntimeError(
            "The EUMETSAT service returned a non-image response:\n" + error_text[:4000]
        )

    if not data:
        raise RuntimeError("The EUMETSAT service returned an empty image response.")

    return data


def download_rendered_layer(request_spec, output_width, output_height):
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Local image composition requires Pillow. Install it with "
            "'python -m pip install -r requirements.txt'."
        ) from exc

    data = download_image(request_spec["url"], request_spec["label"])
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            layer_image = source.convert("RGBA")
    except Exception as exc:
        raise RuntimeError(
            f"Could not decode WMS layer image: {request_spec['label']}"
        ) from exc

    if layer_image.size != (output_width, output_height):
        raise RuntimeError(
            f"Layer {request_spec['label']} returned {layer_image.size[0]} x "
            f"{layer_image.size[1]} instead of {output_width} x {output_height}."
        )

    opacity = request_spec["opacity"]
    if opacity != 1.0:
        alpha = layer_image.getchannel("A").point(
            lambda value: round(value * opacity)
        )
        layer_image.putalpha(alpha)

    return layer_image, len(data)


def compose_rendered_layers(requests, output_width, output_height):
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Per-layer opacity/local rendering requires Pillow. Install it with "
            "'python -m pip install -r requirements.txt'."
        ) from exc

    red, green, blue = parse_background_color(BACKGROUND_COLOR)
    canvas = Image.new("RGBA", (output_width, output_height), (red, green, blue, 255))
    total_downloaded = 0

    index = 0
    while index < len(requests):
        DOWNLOAD_PROGRESS.raise_if_cancelled()
        request_spec = requests[index]
        if request_spec.get("role") == "gap_fill":
            end = index
            while end < len(requests) and requests[end].get("role") == "gap_fill":
                end += 1
            group = requests[index:end]
            # The plan is chronological for deterministic URLs and cache keys,
            # but fetch the newest mandatory image first. Older images are only
            # optional material for transparent No Data pixels.
            layer_image, downloaded_size = download_rendered_layer(
                group[-1], output_width, output_height
            )
            total_downloaded += downloaded_size
            for older_request in reversed(group[:-1]):
                DOWNLOAD_PROGRESS.raise_if_cancelled()
                if layer_image.getchannel("A").getextrema() == (255, 255):
                    break
                try:
                    older_image, downloaded_size = download_rendered_layer(
                        older_request, output_width, output_height
                    )
                except DownloadCancelledError:
                    raise
                except Exception as exc:
                    log(
                        "Skipping optional EUMETSAT gap-fill pass "
                        f"{older_request['label']}: {exc}"
                    )
                    continue
                total_downloaded += downloaded_size
                layer_image = Image.alpha_composite(older_image, layer_image)
            canvas = Image.alpha_composite(canvas, layer_image)
            index = end
            continue
        layer_image, downloaded_size = download_rendered_layer(
            request_spec,
            output_width,
            output_height,
        )
        total_downloaded += downloaded_size
        canvas = Image.alpha_composite(canvas, layer_image)
        index += 1

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=False)
    return output.getvalue(), total_downloaded


def compose_truecolor_black_night(requests, output_width, output_height):
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "TrueColor night-side masking requires Pillow. Install it with "
            "'python -m pip install -r requirements.txt'."
        ) from exc

    if not requests or requests[0].get("role") != "earth_mask":
        raise RuntimeError("TrueColor render plan has no Earth mask.")

    red, green, blue = parse_background_color(BACKGROUND_COLOR)
    canvas = Image.new("RGBA", (output_width, output_height), (red, green, blue, 255))
    total_downloaded = 0

    earth_image, downloaded_size = download_rendered_layer(
        requests[0],
        output_width,
        output_height,
    )
    total_downloaded += downloaded_size
    canvas.paste((0, 0, 0, 255), (0, 0), earth_image.getchannel("A"))

    for request_spec in requests[1:]:
        layer_image, downloaded_size = download_rendered_layer(
            request_spec,
            output_width,
            output_height,
        )
        total_downloaded += downloaded_size
        canvas = Image.alpha_composite(canvas, layer_image)

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=False)
    return output.getvalue(), total_downloaded


def render_image(render_mode, requests, output_width, output_height):
    if render_mode == "server":
        data = download_image(requests[0]["url"])
        return data, len(data)
    if render_mode == "truecolor_black_night":
        return compose_truecolor_black_night(
            requests,
            output_width,
            output_height,
        )
    return compose_rendered_layers(requests, output_width, output_height)


def resize_rendered_image(data, output_width, output_height):
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Image resizing requires Pillow. Install it with "
            "'python -m pip install -r requirements.txt'."
        ) from exc

    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            if source.size == (output_width, output_height):
                return data
            resized = source.convert("RGB").resize(
                (output_width, output_height),
                Image.Resampling.LANCZOS,
            )
    except Exception as exc:
        raise RuntimeError("Could not resize the rendered WMS image.") from exc

    output = io.BytesIO()
    resized.save(output, format="PNG", optimize=False)
    return output.getvalue()


def generate_history_path():
    timestamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    candidate = HISTORY_DIR / f"{HISTORY_FILENAME_PREFIX}_{timestamp}.png"
    counter = 1

    while candidate.exists():
        candidate = HISTORY_DIR / (
            f"{HISTORY_FILENAME_PREFIX}_{timestamp}_{counter}.png"
        )
        counter += 1

    return candidate


def get_latest_image_files():
    """Return image files in the latest folder, newest first."""
    files = [path for path in LATEST_DIR.glob("*.png") if path.is_file()]
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def generate_latest_path():
    """Create an unused timestamped path for the newest image."""
    timestamp = dt.datetime.now().replace(microsecond=0)

    while True:
        candidate = LATEST_DIR / (
            f"{LATEST_FILENAME_PREFIX}_{timestamp.strftime('%Y-%m-%d_%H%M%S')}.png"
        )
        if not candidate.exists():
            return candidate
        timestamp += dt.timedelta(seconds=1)


def _latest_state_path():
    return LATEST_DIR / LATEST_STATE_FILENAME


def _write_latest_state(path, configuration_signature, output_size,
                        source_signature=None, source_time=None):
    if configuration_signature is None:
        return
    width, height = map(int, output_size)
    payload = {
        "version": 2,
        "file": path.name,
        "configuration_hash": signature_digest(configuration_signature),
        "source_hash": (
            signature_digest(source_signature)
            if source_signature is not None else None
        ),
        "image_hash": calculate_file_sha256(path),
        "width": width,
        "height": height,
        "source_time": source_time,
    }
    state_path = _latest_state_path()
    temporary = state_path.with_name(state_path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, state_path)
    finally:
        temporary.unlink(missing_ok=True)


def reusable_latest_image(configuration_signature, output_size,
                          source_signature=None, source_time=None,
                          allow_legacy_source_time=False):
    """Return a verified normal image matching its settings and source frame."""
    state_path = _latest_state_path()
    try:
        if state_path.stat().st_size > 64_000:
            return None
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        if payload.get("version") not in (1, 2):
            return None
        if payload.get("configuration_hash") != signature_digest(configuration_signature):
            return None
        if source_signature is not None:
            source_matches = (
                payload.get("version") == 2
                and payload.get("source_hash") == signature_digest(source_signature)
            )
            legacy_time_matches = (
                allow_legacy_source_time and payload.get("version") == 1
                and source_time is not None
                and payload.get("source_time") == source_time
            )
            if not source_matches and not legacy_time_matches:
                return None
        width, height = map(int, output_size)
        if (payload.get("width"), payload.get("height")) != (width, height):
            return None
        filename = payload.get("file")
        if not isinstance(filename, str) or Path(filename).name != filename:
            return None
        path = (LATEST_DIR / filename).resolve()
        if path.parent != LATEST_DIR.resolve() or not path.is_file():
            return None
        if calculate_file_sha256(path) != payload.get("image_hash"):
            return None
        from PIL import Image
        with Image.open(path) as image:
            if image.format != "PNG" or image.size != (width, height):
                return None
            image.verify()
        return path
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def save_latest_image(data, configuration_signature=None, output_size=None,
                      source_signature=None, source_time=None):
    new_hash = calculate_sha256(data)
    existing_files = get_latest_image_files()
    current_path = existing_files[0] if existing_files else None

    if current_path is not None:
        old_hash = calculate_file_sha256(current_path)
        if new_hash == old_hash:
            if output_size is not None:
                _write_latest_state(
                    current_path, configuration_signature, output_size,
                    source_signature, source_time
                )
            log("No image change detected. Latest file remains unchanged.")
            return None

    latest_path = generate_latest_path()
    temp_path = latest_path.with_suffix(latest_path.suffix + ".tmp")
    temp_path.write_bytes(data)

    archived_path = None
    installed = False

    try:
        if current_path is not None and ENABLE_HISTORY:
            with HISTORY_LOCK:
                archived_path = generate_history_path()
                shutil.copy2(current_path, archived_path)

        os.replace(temp_path, latest_path)
        installed = True

        # Keep exactly one PNG in the latest folder. Install the new path first
        # so the folder is never temporarily empty.
        for old_path in existing_files:
            old_path.unlink(missing_ok=True)
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        if not installed and archived_path is not None and archived_path.exists():
            archived_path.unlink(missing_ok=True)
        raise

    if archived_path is not None:
        log(f"Archived previous image: {archived_path}")

    log(f"Installed new latest image: {latest_path}")
    log(f"Final image size: {format_bytes(len(data))}")
    if output_size is not None:
        _write_latest_state(
            latest_path, configuration_signature, output_size,
            source_signature, source_time
        )
    return latest_path


def save_profile_image(profile_id, configuration_signature, source_signature,
                       data, output_size, source_time=None):
    """Install an immutable cached profile image and archive its predecessor."""
    cache = get_profile_cache()
    current_before = cache.current(profile_id)
    current_path, previous_path = cache.install(
        profile_id, configuration_signature, source_signature, data, output_size,
        source_time=source_time,
    )
    if previous_path is not None and ENABLE_HISTORY:
        with HISTORY_LOCK:
            archived_path = generate_history_path()
            shutil.copy2(previous_path, archived_path)
        log(f"Archived previous profile image: {archived_path}")
    cache.prune()
    if current_before == current_path:
        log("No profile image change detected. Cached file remains unchanged.")
        return None, current_path
    log(f"Installed profile image in cache: {current_path}")
    log(f"Final image size: {format_bytes(len(data))}")
    return current_path, current_path


# =============================================================================
# HISTORY RETENTION
# =============================================================================


def get_history_files():
    return [path for path in HISTORY_DIR.glob(f"{HISTORY_FILENAME_PREFIX}_*.png")
            if path.is_file()]


def clear_history_images():
    """Remove only MarbleScape-managed history PNGs and report reclaimed space."""
    with HISTORY_LOCK:
        files = get_history_files()
        total_bytes = 0
        removed = 0
        for path in files:
            try:
                total_bytes += path.stat().st_size
                path.unlink()
                removed += 1
            except FileNotFoundError:
                continue
        return {"files": removed, "bytes": total_bytes}


def subtract_calendar_period(value):
    months_to_subtract = HISTORY_RETENTION_YEARS * 12 + HISTORY_RETENTION_MONTHS
    absolute_month = value.year * 12 + (value.month - 1) - months_to_subtract
    target_year = absolute_month // 12
    target_month = absolute_month % 12 + 1
    target_day = min(value.day, calendar.monthrange(target_year, target_month)[1])

    shifted = value.replace(
        year=target_year,
        month=target_month,
        day=target_day,
    )

    return shifted - dt.timedelta(
        days=HISTORY_RETENTION_DAYS,
        hours=HISTORY_RETENTION_HOURS,
        minutes=HISTORY_RETENTION_MINUTES,
    )


def cleanup_history():
    if not ENABLE_HISTORY:
        return 0

    with HISTORY_LOCK:
        removed = 0
        files = get_history_files()

        if HISTORY_RETENTION_MODE in {"time", "both"}:
            cutoff = subtract_calendar_period(dt.datetime.now())
            for path in list(files):
                modified = dt.datetime.fromtimestamp(path.stat().st_mtime)
                if modified < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1

            files = get_history_files()

        if HISTORY_RETENTION_MODE in {"count", "both"}:
            files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            for path in files[HISTORY_MAX_FILES:]:
                path.unlink(missing_ok=True)
                removed += 1

    if removed:
        log(f"History cleanup removed {removed} file(s).")

    return removed


# =============================================================================
# STORAGE ESTIMATE
# =============================================================================


def estimated_time_retention_slots():
    now = dt.datetime.now()
    cutoff = subtract_calendar_period(now)
    retention_seconds = max(0.0, (now - cutoff).total_seconds())
    interval_seconds = UPDATE_INTERVAL_MINUTES * 60.0
    return int(math.ceil(retention_seconds / interval_seconds))


def estimated_history_slots():
    if not ENABLE_HISTORY:
        return 0

    if HISTORY_RETENTION_MODE == "count":
        return HISTORY_MAX_FILES

    time_slots = estimated_time_retention_slots()

    if HISTORY_RETENTION_MODE == "time":
        return time_slots

    return min(HISTORY_MAX_FILES, time_slots)


def print_storage_estimate(image_size):
    history_slots = estimated_history_slots()
    profile_slots = len(IMAGE_PROFILE_LIBRARY.get("items", ()))
    total_slots = history_slots + profile_slots + 1
    expected_bytes = image_size * total_slots

    log()
    log("Storage estimate based on the first downloaded image:")
    log(f"  Current image size: {format_bytes(image_size)}")
    log(f"  Estimated retained history images: {history_slots}")
    log("  Normal latest images: 1")
    log(f"  Cached profile images: up to {profile_slots}")
    log(f"  Estimated maximum total: {format_bytes(expected_bytes)}")

    if HISTORY_RETENTION_MODE in {"time", "both"}:
        log(
            "  Time-based estimate assumes every polling cycle produces a new "
            "image and is therefore a conservative upper-bound estimate."
        )

    log()


def get_storage_status():
    try:
        latest_files = get_latest_image_files()
    except OSError:
        latest_files = []
    try:
        history_files = get_history_files()
    except OSError:
        history_files = []

    def total_file_size(paths):
        total = 0
        for path in paths:
            try:
                total += path.stat().st_size
            except (FileNotFoundError, OSError):
                continue
        return total

    current_image_size = None
    current_path = get_current_image_path()
    if current_path is not None:
        try:
            current_image_size = current_path.stat().st_size
        except (FileNotFoundError, OSError):
            pass
    elif latest_files:
        try:
            current_image_size = latest_files[0].stat().st_size
        except (FileNotFoundError, OSError):
            pass

    estimated_history_images = estimated_history_slots()
    profile_count = len(IMAGE_PROFILE_LIBRARY.get("items", ()))
    estimated_maximum_images = estimated_history_images + 1 + profile_count
    estimated_total_bytes = (
        current_image_size * estimated_maximum_images
        if current_image_size is not None
        else None
    )
    try:
        cache_status = get_profile_cache().status()
    except Exception:
        cache_status = {"profiles": 0, "files": 0, "bytes": 0}
    used_bytes = total_file_size((*latest_files, *history_files)) + cache_status["bytes"]

    return {
        "latest_images": len(latest_files),
        "current_image_size": current_image_size,
        "estimated_history_images": estimated_history_images,
        "estimated_maximum_images": estimated_maximum_images,
        "estimated_total_bytes": estimated_total_bytes,
        "used_bytes": used_bytes,
        "cache_profiles": cache_status["profiles"],
        "cache_files": cache_status["files"],
        "cache_bytes": cache_status["bytes"],
    }


# =============================================================================
# WINDOWS DESKTOP WALLPAPER
# =============================================================================


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class WindowsRect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def check_hresult(result, operation):
    if result < 0:
        unsigned = result & 0xFFFFFFFF
        raise OSError(f"{operation} failed with HRESULT 0x{unsigned:08X}")


def get_com_method(interface_pointer, index, restype, *argtypes):
    vtable = ctypes.cast(
        interface_pointer,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
    ).contents
    prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return prototype(vtable[index])


def release_com_pointer(interface_pointer):
    if not interface_pointer:
        return
    release = get_com_method(interface_pointer, 2, ctypes.c_ulong)
    release(interface_pointer)


def create_desktop_wallpaper_interface():
    ole32 = ctypes.windll.ole32

    clsid_desktop_wallpaper = GUID.from_string(
        "C2CF3110-460E-4FC1-B9D0-8A1C0C9CC4BD"
    )
    iid_desktop_wallpaper = GUID.from_string(
        "B92B56A9-8B55-4E14-9A89-0199BBB6F93B"
    )

    interface_pointer = ctypes.c_void_p()

    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long

    CLSCTX_ALL = 0x17
    result = ole32.CoCreateInstance(
        ctypes.byref(clsid_desktop_wallpaper),
        None,
        CLSCTX_ALL,
        ctypes.byref(iid_desktop_wallpaper),
        ctypes.byref(interface_pointer),
    )
    check_hresult(result, "CoCreateInstance(IDesktopWallpaper)")
    return interface_pointer


def with_windows_com(callback):
    if os.name != "nt":
        return callback()

    ole32 = ctypes.windll.ole32
    ole32.CoInitialize.argtypes = [ctypes.c_void_p]
    ole32.CoInitialize.restype = ctypes.c_long
    result = ole32.CoInitialize(None)

    initialized = result >= 0
    if result < 0:
        check_hresult(result, "CoInitialize")

    try:
        return callback()
    finally:
        if initialized:
            ole32.CoUninitialize()


def list_windows_wallpaper_monitors():
    if os.name != "nt":
        return []

    def read_monitors():
        desktop = create_desktop_wallpaper_interface()
        try:
            get_count = get_com_method(desktop, 6, ctypes.c_long,
                                       ctypes.POINTER(ctypes.c_uint))
            get_id = get_com_method(desktop, 5, ctypes.c_long, ctypes.c_uint,
                                    ctypes.POINTER(ctypes.c_void_p))
            get_rect = get_com_method(desktop, 7, ctypes.c_long, ctypes.c_wchar_p,
                                      ctypes.POINTER(WindowsRect))
            free_memory = ctypes.windll.ole32.CoTaskMemFree
            free_memory.argtypes = [ctypes.c_void_p]
            free_memory.restype = None
            count = ctypes.c_uint()
            check_hresult(get_count(desktop, ctypes.byref(count)),
                          "IDesktopWallpaper.GetMonitorDevicePathCount")
            monitors = []
            for index in range(count.value):
                raw_id = ctypes.c_void_p()
                check_hresult(get_id(desktop, index, ctypes.byref(raw_id)),
                              "IDesktopWallpaper.GetMonitorDevicePathAt")
                try:
                    monitor_id = ctypes.wstring_at(raw_id.value)
                finally:
                    free_memory(raw_id)
                rectangle = WindowsRect()
                result = get_rect(desktop, monitor_id, ctypes.byref(rectangle))
                if result == 1 or result == ctypes.c_long(0x80004005).value:
                    continue
                check_hresult(result, "IDesktopWallpaper.GetMonitorRECT")
                if rectangle.right <= rectangle.left or rectangle.bottom <= rectangle.top:
                    continue
                monitors.append({
                    "id": monitor_id,
                    "rect": (rectangle.left, rectangle.top,
                             rectangle.right, rectangle.bottom),
                })
            return monitors
        finally:
            release_com_pointer(desktop)

    return with_windows_com(read_monitors)


def acquire_windows_single_instance(wait_seconds=0):
    global WINDOWS_SINGLE_INSTANCE_HANDLE
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                       ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    deadline = time.monotonic() + max(0, float(wait_seconds))
    while True:
        handle = kernel32.CreateMutexW(
            None, False, "Global\\Gittegatt.MarbleScape.7A87D1D4-80E9-4F7E-91EC-DF10A4969899"
        )
        error = ctypes.get_last_error()
        if handle and error != 183:
            WINDOWS_SINGLE_INSTANCE_HANDLE = handle
            return True
        if handle:
            kernel32.CloseHandle(handle)
        elif error != 5:
            raise ctypes.WinError(error)
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0, min(0.1, deadline - time.monotonic())))


def compose_monitor_wallpaper(source, mode, rect, all_rects, background_color):
    from PIL import Image

    width, height = rect[2] - rect[0], rect[3] - rect[1]
    if width <= 0 or height <= 0:
        raise ValueError("Monitor dimensions must be positive.")
    if mode == "span":
        left = min(item[0] for item in all_rects)
        top = min(item[1] for item in all_rects)
        right = max(item[2] for item in all_rects)
        bottom = max(item[3] for item in all_rects)
        span_width, span_height = right - left, bottom - top
        # Crop in source coordinates before resizing to avoid a full virtual-desktop buffer.
        source_box = ((rect[0] - left) * source.width / span_width,
                      (rect[1] - top) * source.height / span_height,
                      (rect[2] - left) * source.width / span_width,
                      (rect[3] - top) * source.height / span_height)
        return source.crop(source_box).resize((width, height), Image.Resampling.LANCZOS)
    if mode == "stretch":
        return source.resize((width, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), background_color)
    if mode == "tile":
        for y in range(0, height, source.height):
            for x in range(0, width, source.width):
                canvas.paste(source, (x, y))
        return canvas
    if mode == "center":
        canvas.paste(source, ((width - source.width) // 2,
                              (height - source.height) // 2))
        return canvas
    scale = (min if mode == "fit" else max)(
        width / source.width, height / source.height
    )
    rendered = source.resize((max(1, round(source.width * scale)),
                              max(1, round(source.height * scale))),
                             Image.Resampling.LANCZOS)
    canvas.paste(rendered, ((width - rendered.width) // 2,
                            (height - rendered.height) // 2))
    return canvas


def render_monitor_output_source(source, settings):
    """Size the shared image for one display using available source pixels."""
    from PIL import Image

    width = settings["width"]
    height = settings["height"] or round(width / parse_aspect_ratio(settings["aspect_ratio"]))
    target_size = (width, height)
    if source.size == target_size:
        return source
    quality = settings["render_scale"]
    if quality == "auto":
        sample_size = source.size
    else:
        sample_size = (
            min(source.width, max(width, round(width * min(quality, source.width / width)))),
            min(source.height, max(height, round(height * min(quality, source.height / height)))),
        )
    if sample_size == source.size:
        return source.resize(target_size, Image.Resampling.LANCZOS)
    sampled = source.resize(sample_size, Image.Resampling.LANCZOS)
    try:
        return sampled.resize(target_size, Image.Resampling.LANCZOS)
    finally:
        sampled.close()


def previous_wallpaper_backup(monitor_id):
    directory = CONTENT_DIR / "previous_wallpapers"
    stem = hashlib.sha256(monitor_id.encode("utf-8")).hexdigest()
    for extension in (".png", ".jpg", ".bmp", ".tif", ".webp"):
        candidate = directory / (stem + extension)
        if candidate.is_file():
            return candidate
    return None


def capture_previous_wallpapers(monitors, image_path):
    """Keep each display's current image before MarbleScape replaces it."""
    monitors = [monitor for monitor in monitors
                if previous_wallpaper_backup(monitor["id"]) is None]
    if not monitors:
        return

    def capture():
        desktop = create_desktop_wallpaper_interface()
        try:
            get_wallpaper = get_com_method(
                desktop, 4, ctypes.c_long, ctypes.c_wchar_p,
                ctypes.POINTER(ctypes.c_void_p),
            )
            free_memory = ctypes.windll.ole32.CoTaskMemFree
            free_memory.argtypes = [ctypes.c_void_p]
            free_memory.restype = None
            directory = CONTENT_DIR / "previous_wallpapers"
            for monitor in monitors:
                monitor_id = monitor["id"]
                raw_path = ctypes.c_void_p()
                try:
                    result = get_wallpaper(desktop, monitor_id, ctypes.byref(raw_path))
                    check_hresult(result, "IDesktopWallpaper.GetWallpaper")
                    if result != 0:
                        continue
                    current_path = ctypes.wstring_at(raw_path.value) if raw_path.value else ""
                finally:
                    if raw_path.value:
                        free_memory(raw_path)

                extension = ".png"
                if current_path:
                    source = Path(current_path)
                    resolved = source.resolve()
                    managed_dir = (CONTENT_DIR / "device_wallpapers").resolve()
                    if (resolved == Path(image_path).resolve()
                            or resolved.is_relative_to(managed_dir)
                            or source.name.lower().startswith("marblescape_")):
                        continue
                    from PIL import Image

                    with Image.open(source) as picture:
                        extension = {
                            "PNG": ".png", "JPEG": ".jpg", "BMP": ".bmp",
                            "TIFF": ".tif", "WEBP": ".webp",
                        }.get(picture.format)
                    if extension is None:
                        raise ValueError("Unsupported previous wallpaper image format.")
                else:
                    get_color = get_com_method(
                        desktop, 9, ctypes.c_long, ctypes.POINTER(ctypes.c_uint32),
                    )
                    color = ctypes.c_uint32()
                    check_hresult(get_color(desktop, ctypes.byref(color)),
                                  "IDesktopWallpaper.GetBackgroundColor")

                directory.mkdir(parents=True, exist_ok=True)
                stem = hashlib.sha256(monitor_id.encode("utf-8")).hexdigest()
                destination = directory / (stem + extension)
                temporary = directory / (stem + ".tmp")
                try:
                    if current_path:
                        shutil.copyfile(source, temporary)
                    else:
                        from PIL import Image

                        left, top, right, bottom = monitor["rect"]
                        value = color.value
                        background = Image.new(
                            "RGB", (right - left, bottom - top),
                            (value & 255, (value >> 8) & 255, (value >> 16) & 255),
                        )
                        try:
                            background.save(temporary, format="PNG")
                        finally:
                            background.close()
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
        finally:
            release_com_pointer(desktop)

    with_windows_com(capture)


def restore_previous_wallpaper(monitor_id):
    """Restore one connected display and stop future updates to it."""
    global WINDOWS_WALLPAPER_MONITOR_POSITIONS
    with WINDOWS_WALLPAPER_LOCK:
        backup = previous_wallpaper_backup(monitor_id)
        if backup is None:
            raise FileNotFoundError(
                "No previous wallpaper was saved for this display. MarbleScape "
                "cannot recover a wallpaper replaced before this feature was installed."
            )
        if monitor_id not in {monitor["id"] for monitor in list_windows_wallpaper_monitors()}:
            raise ValueError("The selected display is no longer connected.")

        previous_positions = dict(WINDOWS_WALLPAPER_MONITOR_POSITIONS)
        updated_positions = {**previous_positions, monitor_id: "none"}
        update_active_configuration(lambda text: replace_toml_section_value(
            text, "windows", "monitor_positions", json.dumps(updated_positions),
        ))
        WINDOWS_WALLPAPER_MONITOR_POSITIONS = updated_positions

        def restore():
            desktop = create_desktop_wallpaper_interface()
            try:
                set_wallpaper = get_com_method(
                    desktop, 3, ctypes.c_long, ctypes.c_wchar_p, ctypes.c_wchar_p,
                )
                check_hresult(
                    set_wallpaper(desktop, monitor_id, str(backup.resolve())),
                    "IDesktopWallpaper.SetWallpaper",
                )
            finally:
                release_com_pointer(desktop)

        try:
            with_windows_com(restore)
        except Exception as restore_error:
            WINDOWS_WALLPAPER_MONITOR_POSITIONS = previous_positions
            try:
                update_active_configuration(lambda text: replace_toml_section_value(
                    text, "windows", "monitor_positions", json.dumps(previous_positions),
                ))
            except Exception as rollback_error:
                raise RuntimeError(
                    f"{restore_error} Configuration rollback also failed: "
                    f"{rollback_error}"
                ) from restore_error
            raise


def set_windows_wallpaper(image_path):
    with WINDOWS_WALLPAPER_LOCK:
        _set_windows_wallpaper_unlocked(image_path)


def _set_windows_wallpaper_unlocked(image_path):
    if os.name != "nt":
        return

    position_name = WINDOWS_WALLPAPER_POSITION.lower()
    monitor_positions = {
        monitor_id: mode for monitor_id, mode in WINDOWS_WALLPAPER_MONITOR_POSITIONS.items()
        if mode != position_name
    }
    monitor_outputs = {
        monitor_id: values for monitor_id, values in WINDOWS_WALLPAPER_MONITOR_OUTPUTS.items()
        if values
    }
    if position_name == "none" and not monitor_positions:
        return
    monitors = list_windows_wallpaper_monitors()
    if (monitor_positions or monitor_outputs) and not monitors:
        raise RuntimeError("No connected display could be identified for per-display wallpaper settings.")
    per_display = bool(monitors and (monitor_positions or monitor_outputs))
    selected = [monitor for monitor in monitors
                if monitor_positions.get(monitor["id"], position_name) != "none"]
    if per_display and not selected:
        return
    for monitor in selected:
        try:
            capture_previous_wallpapers([monitor], image_path)
        except Exception as exc:
            log(f"Unable to save previous wallpaper: {exc}")
    position = WINDOWS_WALLPAPER_POSITIONS.get(position_name, 2)
    device_files = {}
    if per_display:
        from PIL import Image

        target_dir = CONTENT_DIR / "device_wallpapers"
        target_dir.mkdir(parents=True, exist_ok=True)
        with Image.open(image_path) as image:
            source = image.convert("RGB")
        try:
            rects = [monitor["rect"] for monitor in monitors]
            for monitor in selected:
                monitor_id = monitor["id"]
                mode = monitor_positions.get(monitor_id, position_name)
                settings = {
                    "width": WIDTH, "height": HEIGHT or 0,
                    "aspect_ratio": ASPECT_RATIO,
                    "render_scale": get_render_scale_setting(),
                    "background_color": BACKGROUND_COLOR,
                }
                settings.update(monitor_outputs.get(monitor_id, {}))
                monitor_source = render_monitor_output_source(source, settings)
                try:
                    composed = compose_monitor_wallpaper(
                        monitor_source, mode, monitor["rect"], rects,
                        parse_background_color(settings["background_color"]),
                    )
                finally:
                    if monitor_source is not source:
                        monitor_source.close()
                filename = hashlib.sha256(monitor_id.encode("utf-8")).hexdigest()[:16] + ".png"
                output_file = target_dir / filename
                temporary = output_file.with_suffix(".tmp")
                try:
                    composed.save(temporary, format="PNG")
                    os.replace(temporary, output_file)
                finally:
                    temporary.unlink(missing_ok=True)
                    composed.close()
                device_files[monitor_id] = output_file
        finally:
            source.close()

    def apply_wallpaper():
        desktop_wallpaper = None

        try:
            desktop_wallpaper = create_desktop_wallpaper_interface()

            # IDesktopWallpaper vtable indices include the three IUnknown methods.
            set_wallpaper = get_com_method(
                desktop_wallpaper,
                3,
                ctypes.c_long,
                ctypes.c_wchar_p,
                ctypes.c_wchar_p,
            )
            set_position = get_com_method(
                desktop_wallpaper,
                10,
                ctypes.c_long,
                ctypes.c_int,
            )
            target_position = 2 if device_files else position
            if device_files and len(selected) < len(monitors):
                get_position = get_com_method(
                    desktop_wallpaper, 11, ctypes.c_long,
                    ctypes.POINTER(ctypes.c_int),
                )
                current_position = ctypes.c_int()
                check_hresult(
                    get_position(desktop_wallpaper, ctypes.byref(current_position)),
                    "IDesktopWallpaper.GetPosition",
                )
                if current_position.value in range(5):
                    target_position = current_position.value
            check_hresult(
                set_position(desktop_wallpaper, target_position),
                "IDesktopWallpaper.SetPosition",
            )
            if device_files:
                for monitor_id, output_file in device_files.items():
                    check_hresult(
                        set_wallpaper(desktop_wallpaper, monitor_id, str(output_file.resolve())),
                        "IDesktopWallpaper.SetWallpaper",
                    )
            else:
                check_hresult(
                    set_wallpaper(desktop_wallpaper, None, str(image_path.resolve())),
                    "IDesktopWallpaper.SetWallpaper",
                )
        finally:
            release_com_pointer(desktop_wallpaper)

    with_windows_com(apply_wallpaper)
    log(f"Windows wallpaper set directly: {image_path}")
    log(f"Windows wallpaper position: {WINDOWS_WALLPAPER_POSITION}")


# =============================================================================
# UPDATE LOOP
# =============================================================================


def get_noaa_client():
    key = (NETWORK_TIMEOUT_SECONDS, USER_AGENT)
    with NOAA_CLIENTS_LOCK:
        if key not in NOAA_CLIENTS:
            NOAA_CLIENTS.clear()
            NOAA_CLIENTS[key] = NOAAClient(timeout=NETWORK_TIMEOUT_SECONDS, user_agent=USER_AGENT)
        return NOAA_CLIENTS[key]


def get_himawari_client():
    key = (NETWORK_TIMEOUT_SECONDS, USER_AGENT)
    with HIMAWARI_CLIENTS_LOCK:
        if key not in HIMAWARI_CLIENTS:
            HIMAWARI_CLIENTS.clear()
            HIMAWARI_CLIENTS[key] = HimawariClient(
                timeout=NETWORK_TIMEOUT_SECONDS, user_agent=USER_AGENT
            )
        return HIMAWARI_CLIENTS[key]


def get_slider_client():
    key = (NETWORK_TIMEOUT_SECONDS, USER_AGENT)
    with SLIDER_CLIENTS_LOCK:
        if key not in SLIDER_CLIENTS:
            SLIDER_CLIENTS.clear()
            SLIDER_CLIENTS[key] = SliderClient(
                timeout=NETWORK_TIMEOUT_SECONDS, user_agent=USER_AGENT
            )
        return SLIDER_CLIENTS[key]


def get_worldview_client():
    key = (NETWORK_TIMEOUT_SECONDS, USER_AGENT)
    with WORLDVIEW_CLIENTS_LOCK:
        if key not in WORLDVIEW_CLIENTS:
            WORLDVIEW_CLIENTS.clear()
            WORLDVIEW_CLIENTS[key] = WorldviewClient(
                timeout=NETWORK_TIMEOUT_SECONDS, user_agent=USER_AGENT
            )
        return WORLDVIEW_CLIENTS[key]


def get_catalogue_client():
    cache_path = (CONTENT_DIR / "catalogues.json").resolve()
    key = (NETWORK_TIMEOUT_SECONDS, USER_AGENT, CATALOGUE_RETRIES, str(cache_path))
    with CATALOGUE_CLIENTS_LOCK:
        if key not in CATALOGUE_CLIENTS:
            CATALOGUE_CLIENTS.clear()
            CATALOGUE_CLIENTS[key] = CatalogueClient(
                noaa=get_noaa_client(), himawari=get_himawari_client(),
                slider=get_slider_client(), worldview=get_worldview_client(),
                timeout=NETWORK_TIMEOUT_SECONDS, user_agent=USER_AGENT,
                cache_path=cache_path, retries=CATALOGUE_RETRIES,
            )
        return CATALOGUE_CLIENTS[key]


def get_copernicus_client():
    key = (COPERNICUS_CLIENT_ID, hashlib.sha256(
        COPERNICUS_CLIENT_SECRET.encode("utf-8")
    ).hexdigest(), float(NETWORK_TIMEOUT_SECONDS), USER_AGENT)
    with COPERNICUS_CLIENTS_LOCK:
        if key not in COPERNICUS_CLIENTS:
            COPERNICUS_CLIENTS.clear()
            COPERNICUS_CLIENTS[key] = CopernicusClient(
                COPERNICUS_CLIENT_ID,
                COPERNICUS_CLIENT_SECRET,
                timeout=NETWORK_TIMEOUT_SECONDS,
                user_agent=USER_AGENT,
                network_attempts=1,
            )
        return COPERNICUS_CLIENTS[key]


def warm_public_catalogues():
    """Refresh shared metadata once per application start, without delaying images."""
    client = get_catalogue_client()
    def worker():
        try:
            result = client.refresh_all_catalogues(refresh=True, startup=True)
            if result.get("errors"):
                log("Catalogue refresh: " + "; ".join(result["errors"]))
        except Exception as exc:
            log(f"Catalogue refresh warning: {exc}")
        if COPERNICUS_CLIENT_ID and COPERNICUS_CLIENT_SECRET:
            problem = None
            for _attempt in range(CATALOGUE_RETRIES + 1):
                try:
                    profile = SOURCE_PROFILES["copernicus"]
                    output_size = get_output_dimensions()
                    cached = client.cached_copernicus_dates(profile, output_size)
                    since = cached[0] if cached else None
                    dates = get_copernicus_client().list_dates(
                        profile, output_size, since=since
                    )
                    if cached:
                        dates = sorted(set(cached) | set(dates), reverse=True)[:MAX_CATALOGUE_DATES]
                    client.store_copernicus_dates(profile, output_size, dates)
                    problem = None
                    break
                except Exception as exc:
                    problem = exc
            if problem is not None:
                cached = client.cached_copernicus_dates(
                    SOURCE_PROFILES["copernicus"], get_output_dimensions()
                )
                suffix = " Cached catalogue data remains available." if cached is not None else ""
                log(f"Copernicus catalogue refresh warning: {problem}.{suffix}")
    threading.Thread(target=worker, name="MarbleScape-catalogue-startup", daemon=True).start()


def choose_automatic_source_resolution(client, provider, profile, output_size,
                                       fit_mode=None, zoom=None):
    """Choose the smallest listed source that avoids enlarging visible pixels."""
    if not callable(getattr(client, "list_products", None)):
        return "largest"
    products = client.list_products(provider, profile["area"], refresh=False)
    product = next(
        (item for item in products if item.get("id") == profile["product"]), None
    )
    if product is None:
        # ``latest(..., 'largest')`` remains an offline-compatible fallback for
        # clients whose catalogue is temporarily unavailable or test doubles
        # that only implement image lookup.
        return "largest"
    resolutions = []
    for value in product.get("resolutions", ()):
        match = re.fullmatch(r"([1-9]\d*)x([1-9]\d*)", str(value))
        if match:
            resolutions.append((str(value), int(match.group(1)), int(match.group(2))))
    if not resolutions:
        raise RuntimeError("The selected product has no supported source resolution.")
    output_width, output_height = map(int, output_size)
    fit_mode = VIEW_MODE if fit_mode is None else fit_mode
    zoom = ZOOM if zoom is None else float(zoom)
    scale_function = min if fit_mode == "fit" else max
    sufficient = [
        item for item in resolutions
        if scale_function(output_width / item[1], output_height / item[2]) * zoom <= 1.0
    ]
    candidates = sufficient or resolutions
    selected = min(candidates, key=lambda item: (item[1] * item[2], item[1], item[2])) \
        if sufficient else max(candidates, key=lambda item: (item[1] * item[2], item[1], item[2]))
    return selected[0]


def automatic_source_output_size(output_size):
    """Cover the configured image and every active Windows display."""
    width, height = map(int, output_size)
    if os.name != "nt" or not SET_WINDOWS_WALLPAPER:
        return width, height
    try:
        monitors = list_windows_wallpaper_monitors()
    except Exception as exc:
        log(f"Unable to size automatic source resolution for monitors: {exc}")
        return width, height
    if len(monitors) < 2:
        return width, height
    display_width = display_height = 0
    for monitor in monitors:
        monitor_id = monitor["id"]
        if WINDOWS_WALLPAPER_MONITOR_POSITIONS.get(
            monitor_id, WINDOWS_WALLPAPER_POSITION
        ) == "none":
            continue
        left, top, right, bottom = monitor["rect"]
        display_width = max(display_width, right - left)
        display_height = max(display_height, bottom - top)
        output = WINDOWS_WALLPAPER_MONITOR_OUTPUTS.get(monitor_id, {})
        if output:
            device_width = int(output.get("width", WIDTH))
            device_height = int(output.get("height", HEIGHT or 0))
            device_ratio = parse_aspect_ratio(output.get("aspect_ratio", ASPECT_RATIO))
            display_width = max(display_width, device_width)
            display_height = max(
                display_height, device_height or round(device_width / device_ratio)
            )
    return (display_width, display_height) if display_width and display_height else (width, height)


def _resolved_profile_resolution(client, provider, profile, output_size):
    if profile["resolution"] != "auto":
        return profile["resolution"]
    return choose_automatic_source_resolution(
        client, provider, profile, automatic_source_output_size(output_size),
        VIEW_MODE, ZOOM,
    )


def get_noaa_frame(output_size):
    profile = SOURCE_PROFILES[IMAGE_SOURCE]
    client = get_noaa_client()
    resolution = _resolved_profile_resolution(client, IMAGE_SOURCE, profile, output_size)
    return client.latest(
        IMAGE_SOURCE, profile["area"], profile["product"], resolution
    )


def get_himawari_frame(output_size):
    profile = SOURCE_PROFILES["himawari"]
    client = get_himawari_client()
    resolution = _resolved_profile_resolution(client, "himawari", profile, output_size)
    return client.latest(
        "himawari", profile["area"], profile["product"], resolution
    )


def get_slider_frame(output_size):
    profile = SOURCE_PROFILES["slider"]
    client = get_slider_client()
    resolution = _resolved_profile_resolution(client, "slider", profile, output_size)
    return client.latest(
        "slider", profile["area"], profile["product"], resolution
    )


def get_worldview_frame(output_size):
    profile = SOURCE_PROFILES["worldview"]
    client = get_worldview_client()
    resolution = _resolved_profile_resolution(client, "worldview", profile, output_size)
    return client.latest(
        "worldview", profile["area"], profile["product"], resolution
    )


def get_copernicus_frame(output_size):
    return get_copernicus_client().latest(
        SOURCE_PROFILES["copernicus"], output_size
    )


def noaa_frame_signature(frame):
    profile = SOURCE_PROFILES[IMAGE_SOURCE]
    return (IMAGE_SOURCE, profile["area"], profile["product"],
            frame.get("resolution", profile["resolution"]),
            frame["timestamp"], frame["url"])


def himawari_frame_signature(frame):
    profile = SOURCE_PROFILES["himawari"]
    return ("himawari", profile["area"], profile["product"],
            frame.get("resolution", profile["resolution"]),
            frame["timestamp"], frame["url"])


def slider_frame_signature(frame):
    profile = SOURCE_PROFILES["slider"]
    return ("slider", profile["area"], profile["product"],
            frame.get("resolution", profile["resolution"]),
            frame["timestamp"], frame["url"])


def worldview_frame_signature(frame):
    profile = SOURCE_PROFILES["worldview"]
    return ("worldview", profile["area"], profile["product"],
            frame.get("resolution", profile["resolution"]),
            frame["timestamp"], frame["url"])


def copernicus_frame_signature(frame):
    profile = frame["profile"]
    return (
        "copernicus", profile["configuration"], profile["mission"],
        profile["product"], profile["layer"], profile["date"],
        profile["latitude"], profile["longitude"], profile["map_zoom"],
        profile["map_labels"], profile["coverage_mode"], profile["lookback_days"],
        profile["max_cloud_cover"], profile["brightness"],
        frame["timestamp"],
    )


def image_source_status_text(time_zone=None):
    """Describe the installed image, keeping source errors and image age visible."""
    time_zone = DISPLAY_TIME_ZONE if time_zone is None else normalize_time_zone(time_zone)
    with IMAGE_STATUS_LOCK:
        status = dict(IMAGE_STATUS)
    if status["error"]:
        return "Update unavailable; previous image kept. " + status["error"]
    if status["provider"] != IMAGE_SOURCE or not status["timestamp"]:
        return "Waiting for an image from " + SOURCE_LABELS[IMAGE_SOURCE]
    if IMAGE_SOURCE == "copernicus":
        profile = SOURCE_PROFILES["copernicus"]
        coverage = (
            f" | {profile['lookback_days']}-day gap fill"
            if profile["coverage_mode"] == "fill_gaps" else ""
        )
        timestamp_text = str(status["timestamp"])
        if timestamp_text == "timeless":
            return "Copernicus Browser | timeless dataset"
        timestamp = dt.datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
        if status.get("fixed_time"):
            return f"Copernicus Browser | selected acquisition {timestamp:%Y-%m-%d}{coverage}"
        age_days = max(0, (dt.datetime.now(dt.timezone.utc) - timestamp).total_seconds() / 86400)
        return (f"Copernicus Browser | latest acquisition "
                f"{format_display_datetime(timestamp, time_zone)}"
                f" | {age_days:.0f} day(s) old{coverage}")
    if IMAGE_SOURCE == "worldview":
        timestamp_text = str(status["timestamp"])
        if timestamp_text == "timeless":
            return "NASA Worldview | timeless visualization"
        timestamp = dt.datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
        if status.get("fixed_time"):
            return ("NASA Worldview | selected acquisition "
                    + format_display_datetime(timestamp, time_zone))
        age_days = max(0, (dt.datetime.now(dt.timezone.utc) - timestamp).total_seconds() / 86400)
        return ("NASA Worldview | latest acquisition "
                f"{format_display_datetime(timestamp, time_zone)}"
                f" | {age_days:.0f} day(s) old")
    timestamp = dt.datetime.fromisoformat(status["timestamp"].replace("Z", "+00:00"))
    age = max(0, (dt.datetime.now(dt.timezone.utc) - timestamp).total_seconds() / 60)
    stale = age > max(30, status["interval_minutes"] * 3)
    return (f"{SOURCE_LABELS[IMAGE_SOURCE]} | "
            f"{format_display_datetime(timestamp, time_zone, include_seconds=True)}"
            f" | {age:.0f} min old" + (" (delayed)" if stale else ""))


def run_noaa_diagnostics(args):
    """Use the selected NOAA catalog for the existing diagnostic CLI options."""
    client = get_noaa_client()
    profile = SOURCE_PROFILES[IMAGE_SOURCE]
    if args.list_layers is not None or args.export_layers is not None:
        products = client.list_products(IMAGE_SOURCE, profile["area"], refresh=True)
        if args.list_layers is not None:
            words = args.list_layers.casefold().split()
            for product in products:
                if all(word in (product["id"] + " " + product["label"]).casefold() for word in words):
                    print(f'{product["id"]}: {product["label"]} | {", ".join(product["resolutions"])}')
        if args.export_layers is not None:
            args.export_layers.write_text(json.dumps(products, indent=2), encoding="utf-8")
        return
    frame = get_noaa_frame(get_output_dimensions())
    if args.print_urls:
        print(frame["url"])
    if args.validate_config:
        log(f"Configuration is valid against the NOAA catalog; image time: {frame['timestamp']}")


def run_himawari_diagnostics(args):
    """Use the selected Himawari catalogue for diagnostic CLI options."""
    client = get_himawari_client()
    profile = SOURCE_PROFILES["himawari"]
    if args.list_layers is not None or args.export_layers is not None:
        products = client.list_products("himawari", profile["area"], refresh=True)
        if args.list_layers is not None:
            words = args.list_layers.casefold().split()
            for product in products:
                if all(word in (product["id"] + " " + product["label"]).casefold()
                       for word in words):
                    print(f'{product["id"]}: {product["label"]} | {", ".join(product["resolutions"])}')
        if args.export_layers is not None:
            args.export_layers.write_text(json.dumps(products, indent=2), encoding="utf-8")
        return
    frame = get_himawari_frame(get_output_dimensions())
    if args.print_urls:
        print(frame["url"])
    if args.validate_config:
        log(f"Configuration is valid against the Himawari catalogue; image time: {frame['timestamp']}")


def run_slider_diagnostics(args):
    """Use the selected CIRA SLIDER catalogue for diagnostic CLI options."""
    client = get_slider_client()
    profile = SOURCE_PROFILES["slider"]
    if args.list_layers is not None or args.export_layers is not None:
        products = client.list_products("slider", profile["area"], refresh=True)
        if args.list_layers is not None:
            words = args.list_layers.casefold().split()
            for product in products:
                if all(word in (product["id"] + " " + product["label"]).casefold()
                       for word in words):
                    print(f'{product["id"]}: {product["label"]} | {", ".join(product["resolutions"])}')
        if args.export_layers is not None:
            args.export_layers.write_text(json.dumps(products, indent=2), encoding="utf-8")
        return
    frame = get_slider_frame(get_output_dimensions())
    if args.print_urls:
        print(frame["url"])
    if args.validate_config:
        log(f"Configuration is valid against the CIRA SLIDER catalogue; image time: {frame['timestamp']}")


def run_copernicus_diagnostics(args):
    profile = SOURCE_PROFILES["copernicus"]
    product = get_copernicus_product(profile["configuration"], profile["product"])
    layer = get_copernicus_layer(product, profile["layer"])
    if args.list_layers is not None:
        words = args.list_layers.casefold().split()
        for item in product["layers"]:
            text = f"{item['id']} {item['name']} {item['data_type']}"
            if all(word in text.casefold() for word in words):
                print(f"{item['id']}: {item['name']} | {item['data_type']}")
    if args.export_layers is not None:
        args.export_layers.write_text(json.dumps(product["layers"], indent=2), encoding="utf-8")
    if args.print_urls:
        print(COPERNICUS_PROCESS_URL)
    if args.validate_config:
        width, height = get_output_dimensions()
        frame = get_copernicus_frame((width, height))
        log("Configuration is valid against the Copernicus catalogue; image time: " + frame["timestamp"])


def run_worldview_diagnostics(args):
    client = get_worldview_client()
    if args.list_layers is not None or args.export_layers is not None:
        layers = client.list_areas("worldview", refresh=True)
        if args.list_layers is not None:
            words = args.list_layers.casefold().split()
            for layer in layers:
                text = f"{layer['id']} {layer['label']} {layer['category']}"
                if all(word in text.casefold() for word in words):
                    print(f"{layer['id']}: {layer['label']} | {layer['matrix_set']}")
        if args.export_layers is not None:
            args.export_layers.write_text(json.dumps(layers, indent=2), encoding="utf-8")
        return
    frame = get_worldview_frame(get_output_dimensions())
    if args.print_urls:
        print(frame["url"])
    if args.validate_config:
        log("Configuration is valid against the NASA GIBS catalogue; image time: "
            + frame["timestamp"])


def print_configuration(
    output_width,
    output_height,
    render_width,
    render_height,
    effective_render_scale,
    bbox,
    projection_name,
    projection,
    resolved_layers,
    render_mode,
):
    log(f"MarbleScape Wallpaper Downloader {VERSION}")
    log("--------------------------------")
    if render_mode in {"noaa", "himawari", "slider", "worldview"}:
        profile = SOURCE_PROFILES[IMAGE_SOURCE]
        log(f"Source: {SOURCE_LABELS[IMAGE_SOURCE]}")
        if render_mode == "worldview":
            log(f"Layer: {profile['area']} | Date: {profile['product']}")
        else:
            log(f"Area: {profile['area']} | Product: {profile['product']}")
        log(f"Source size: {profile['resolution']} | Output: {output_width} x {output_height}")
        time_label = ("Latest available" if profile["product"] == "latest"
                      else "Fixed " + profile["product"])
        log(f"View: {VIEW_MODE}, zoom {ZOOM:g} | {time_label} still image")
        log(f"Update interval: {UPDATE_INTERVAL_MINUTES:g} minute(s)")
        log(f"Latest directory: {LATEST_DIR}")
        return
    if render_mode == "copernicus":
        profile = SOURCE_PROFILES["copernicus"]
        product = get_copernicus_product(profile["configuration"], profile["product"])
        layer = get_copernicus_layer(product, profile["layer"])
        log("Source: Copernicus Browser / Sentinel Hub")
        log(f"Mission: {profile['mission']} | Configuration: {profile['configuration']}")
        log(f"Product: {product['name']} | Layer: {layer['name']}")
        log(f"Location: {profile['latitude']:g}, {profile['longitude']:g} | map zoom {profile['map_zoom']}")
        log(f"Date: {profile['date']} | Output: {output_width} x {output_height}")
        coverage = profile["coverage_mode"]
        if coverage == "fill_gaps":
            coverage += f" ({profile['lookback_days']} days)"
        log(f"Map labels: {profile['map_labels']} | Coverage: {coverage}")
        log(f"Catalogue revision: {copernicus_catalogue_revision()}")
        log(f"Update interval: {UPDATE_INTERVAL_MINUTES:g} minute(s)")
        log(f"Latest directory: {LATEST_DIR}")
        return
    log(f"Projection: {projection_name} ({projection['crs']})")
    log(f"View preset: {VIEW_PRESET}")
    log(f"Aspect ratio: {ASPECT_RATIO}")
    log(f"View mode: {VIEW_MODE}")
    log(f"Zoom: {ZOOM:g}")
    log(f"Output size: {output_width} x {output_height}")
    requested_render_scale = get_requested_render_scale(
        output_width,
        output_height,
    )
    if RENDER_SCALE_AUTOMATIC:
        log(
            "Requested render quality: automatic maximum "
            f"({requested_render_scale:g}x for this output)"
        )
    else:
        log(f"Requested render quality: {RENDER_SCALE:g}x")
    log(
        f"Effective WMS render: {render_width} x {render_height} "
        f"({effective_render_scale:g}x)"
    )
    if requested_render_scale > effective_render_scale + 1e-9:
        log(
            f"Render quality capped at approximately {MAX_WMS_DIMENSION} pixels "
            "per WMS axis."
        )
    if output_width > MAX_WMS_DIMENSION or output_height > MAX_WMS_DIMENSION:
        log(
            "Warning: EUMETSAT may reject dimensions above approximately "
            f"{MAX_WMS_DIMENSION} pixels because of its rendering memory limit."
        )
    log(f"BBOX: {bbox}")
    log(f"Render mode: {render_mode}")
    log(f"Background color: {normalize_background_color(BACKGROUND_COLOR)}")
    log(f"Black TrueColor night side: {TRUECOLOR_BLACK_NIGHT}")
    eumetsat_profile = SOURCE_PROFILES["eumetsat"]
    if eumetsat_profile.get("fill_gaps"):
        log(
            "LEO gap filling: enabled, maximum lookback "
            f"{eumetsat_profile['gap_fill_lookback_hours']} hours"
        )
    else:
        log("LEO gap filling: disabled")
    log(f"Update interval: {UPDATE_INTERVAL_MINUTES:g} minute(s)")
    log(f"Output root: {OUTPUT_ROOT}")
    log(f"Latest directory: {LATEST_DIR}")
    log(f"History directory: {HISTORY_DIR}")
    log(f"History enabled: {ENABLE_HISTORY}")
    if os.name == "nt":
        log(f"Set Windows wallpaper directly: {SET_WINDOWS_WALLPAPER}")
        if SET_WINDOWS_WALLPAPER:
            log(f"Windows wallpaper position: {WINDOWS_WALLPAPER_POSITION}")
    if ENABLE_HISTORY:
        log(f"History retention mode: {HISTORY_RETENTION_MODE}")
    log("Layer order (bottom to top):")
    for index, layer in enumerate(resolved_layers, start=1):
        style = layer["style"] or "default"
        layer_time = layer["time"] or "latest"
        log(
            f"  {index}. {layer['name']} | opacity={layer['opacity']:.2f} | "
            f"style={style} | time={layer_time}"
        )
    log()


def record_source_frame_status(render_mode, requests):
    """Record provider timing after either a download or a persistent cache hit."""
    if render_mode in {"noaa", "himawari", "slider", "worldview"}:
        frame = requests[0]["frame"]
        with IMAGE_STATUS_LOCK:
            IMAGE_STATUS.update(provider=IMAGE_SOURCE, timestamp=frame["timestamp"],
                                interval_minutes=frame.get("expected_interval_seconds", 600) / 60,
                                fixed_time=bool(frame.get("fixed_time")), error="")
    elif render_mode == "copernicus":
        frame = requests[0]["frame"]
        with IMAGE_STATUS_LOCK:
            IMAGE_STATUS.update(
                provider="copernicus", timestamp=frame["timestamp"],
                interval_minutes=UPDATE_INTERVAL_MINUTES,
                fixed_time=not frame["latest"], error="",
            )


def profile_cache_source_time(render_mode, requests, source_signature=None):
    """Return the acquisition time represented by a rendered profile image."""
    if render_mode in {"noaa", "himawari", "slider", "copernicus", "worldview"} and requests:
        timestamp = requests[0].get("frame", {}).get("timestamp")
        if timestamp and timestamp != "timeless":
            return str(timestamp)
    if render_mode in {"server", "local", "truecolor_black_night"}:
        timestamps = []
        for entry in source_signature or ():
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            value = str(entry[1]).strip()
            try:
                parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=dt.timezone.utc)
                timestamps.append(parsed.astimezone(dt.timezone.utc))
            except (ValueError, OverflowError):
                continue
        if timestamps:
            return min(timestamps).isoformat().replace("+00:00", "Z")
    return None


def _perform_update(
    render_mode,
    requests,
    render_width,
    render_height,
    output_width,
    output_height,
    cache_profile_id=None,
    cache_configuration_signature=None,
    cache_source_signature=None,
    cache_source_time=None,
):
    DOWNLOAD_PROGRESS.raise_if_cancelled()
    if render_mode == "noaa":
        data = get_noaa_client().fetch_image(
            requests[0]["frame"], (output_width, output_height),
            fit_mode=VIEW_MODE, zoom=ZOOM, background=BACKGROUND_COLOR,
        )
        downloaded_size = len(data)
    elif render_mode == "himawari":
        data = get_himawari_client().fetch_image(
            requests[0]["frame"], (output_width, output_height),
            fit_mode=VIEW_MODE, zoom=ZOOM, background=BACKGROUND_COLOR,
        )
        downloaded_size = len(data)
    elif render_mode == "slider":
        data = get_slider_client().fetch_image(
            requests[0]["frame"], (output_width, output_height),
            fit_mode=VIEW_MODE, zoom=ZOOM, background=BACKGROUND_COLOR,
        )
        downloaded_size = len(data)
    elif render_mode == "worldview":
        data = get_worldview_client().fetch_image(
            requests[0]["frame"], (output_width, output_height),
            fit_mode=VIEW_MODE, zoom=ZOOM, background=BACKGROUND_COLOR,
        )
        downloaded_size = len(data)
    elif render_mode == "copernicus":
        frame = requests[0]["frame"]
        client = get_copernicus_client()
        data, downloaded_size = client.fetch_image(frame)
        for warning in client.last_render_warnings:
            log(f"Copernicus map overlay warning: {warning}")
    else:
        data, downloaded_size = render_image(
            render_mode,
            requests,
            render_width,
            render_height,
        )
    DOWNLOAD_PROGRESS.raise_if_cancelled()
    if (render_width, render_height) != (output_width, output_height):
        data = resize_rendered_image(data, output_width, output_height)
    DOWNLOAD_PROGRESS.raise_if_cancelled()
    if APPLICATION_STOP_EVENT.is_set():
        raise RuntimeError("Update cancelled because the application is stopping.")
    if CONFIGURATION_RELOAD_EVENT.is_set():
        raise RuntimeError("Update discarded because configuration changed.")
    DOWNLOAD_PROGRESS.seal_cancellation()
    if cache_profile_id is None:
        installed_path = save_latest_image(
            data,
            configuration_signature=cache_configuration_signature,
            output_size=(output_width, output_height),
            source_signature=cache_source_signature,
            source_time=cache_source_time,
        )
        current_path = installed_path
        if current_path is None:
            latest_files = get_latest_image_files()
            current_path = latest_files[0] if latest_files else None
    else:
        installed_path, current_path = save_profile_image(
            cache_profile_id,
            cache_configuration_signature,
            cache_source_signature,
            data,
            (output_width, output_height),
            source_time=cache_source_time,
        )
    record_source_frame_status(render_mode, requests)
    cleanup_history()
    if render_mode in {"local", "truecolor_black_night", "copernicus"}:
        log(f"Total downloaded layer data: {format_bytes(downloaded_size)}")
    return installed_path, current_path, len(data)


def expected_download_request_count(render_mode, requests):
    """Return a reliable response count, or None for dynamic tiled renders."""
    if render_mode in {"server", "noaa", "worldview"}:
        return 1
    if render_mode in {"local", "truecolor_black_night"}:
        return len(requests) or None
    if render_mode in {"himawari", "slider"} and requests:
        if render_mode == "slider":
            return None
        return 1 if requests[0].get("frame", {}).get("kind") == "jma" else None
    return None


class DownloadRetriesExhausted(RuntimeError):
    """A transient image-transfer failure used all configured attempts."""


def is_retryable_download_error(exc):
    """Retry transient transport failures, never invalid requests or bad certificates."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, ssl.SSLCertVerificationError):
            return False
        if isinstance(exc, HTTPError):
            return exc.code in {408, 425, 429, 500, 502, 503, 504}
        if isinstance(exc, URLError):
            reason = getattr(exc, "reason", None)
            return not (isinstance(reason, ssl.SSLCertVerificationError)
                        or "certificate verify failed" in str(reason).casefold())
        if isinstance(exc, (TimeoutError, ConnectionError, http.client.IncompleteRead,
                            http.client.RemoteDisconnected)):
            return True
        if isinstance(exc, OSError) and getattr(exc, "winerror", None) in {
            10051, 10052, 10053, 10054, 10060, 10061,
        }:
            return True
        exc = exc.__cause__
    return False


def wait_before_download_retry(seconds):
    deadline = time.monotonic() + seconds
    while True:
        DOWNLOAD_PROGRESS.raise_if_cancelled()
        if APPLICATION_STOP_EVENT.is_set() or CONFIGURATION_RELOAD_EVENT.is_set():
            raise RuntimeError(
                "Download retry stopped because the application is stopping or settings changed."
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        DOWNLOAD_PROGRESS.wait_or_raise(min(0.25, remaining))


def perform_update(
    render_mode,
    requests,
    render_width,
    render_height,
    output_width,
    output_height,
    cache_profile_id=None,
    cache_configuration_signature=None,
    cache_source_signature=None,
    cache_source_time=None,
):
    """Track network transfer progress while rendering and installing an image."""
    expected_requests = expected_download_request_count(render_mode, requests)
    DOWNLOAD_PROGRESS.begin(expected_requests)
    max_attempts = normalize_download_retries(DOWNLOAD_RETRIES) + 1
    for attempt in range(max_attempts):
        try:
            result = _perform_update(
                render_mode,
                requests,
                render_width,
                render_height,
                output_width,
                output_height,
                cache_profile_id=cache_profile_id,
                cache_configuration_signature=cache_configuration_signature,
                cache_source_signature=cache_source_signature,
                cache_source_time=cache_source_time,
            )
        except DownloadCancelledError:
            DOWNLOAD_PROGRESS.finish(False, cancelled=True)
            raise
        except Exception as exc:
            if (not DOWNLOAD_PROGRESS.snapshot()["cancellable"]
                    or not is_retryable_download_error(exc)):
                DOWNLOAD_PROGRESS.finish(False)
                raise
            if attempt + 1 >= max_attempts:
                DOWNLOAD_PROGRESS.finish(False)
                raise DownloadRetriesExhausted(
                    f"Image download failed after {max_attempts} attempts: {exc}"
                ) from exc
            log(f"Image transfer attempt {attempt + 1}/{max_attempts} failed; retrying: {exc}")
            try:
                wait_before_download_retry(min(10.0, 0.5 * (2 ** attempt)))
                DOWNLOAD_PROGRESS.restart_attempt(expected_requests)
            except DownloadCancelledError:
                DOWNLOAD_PROGRESS.finish(False, cancelled=True)
                raise
            except BaseException:
                DOWNLOAD_PROGRESS.finish(False)
                raise
        except BaseException:
            DOWNLOAD_PROGRESS.finish(False)
            raise
        else:
            DOWNLOAD_PROGRESS.finish(True)
            return result


def report_update_status(status_callback, state, next_check=None):
    if status_callback is None:
        return
    try:
        status_callback(state, next_check)
    except Exception as exc:
        log(f"Tray status update warning: {exc}")


def sleep_until_next_cycle(cycle_started_monotonic, status_callback=None):
    interval_seconds = UPDATE_INTERVAL_MINUTES * 60.0
    elapsed = time.monotonic() - cycle_started_monotonic
    remaining = max(0.0, interval_seconds - elapsed)
    if NEXT_ROTATION_DEADLINE is not None:
        remaining = min(remaining, max(0.0, NEXT_ROTATION_DEADLINE - time.monotonic()))

    next_check = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=remaining)
    report_update_status(status_callback, "waiting", next_check)
    log(f"Next check: {format_display_datetime(next_check, DISPLAY_TIME_ZONE, include_seconds=True)}.")
    deadline = time.monotonic() + remaining
    while not APPLICATION_STOP_EVENT.is_set():
        remaining = deadline - time.monotonic()
        if FORCE_UPDATE_EVENT.is_set() or CONFIGURATION_RELOAD_EVENT.is_set() or remaining <= 0:
            return True
        APPLICATION_STOP_EVENT.wait(min(remaining, 0.25))
    return False


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(
        description=("Download EUMETSAT, NOAA GOES/Solar, Himawari, CIRA SLIDER, "
                     "Copernicus, and NASA Worldview wallpaper images.")
    )
    parser.add_argument("--version", action="version", version=f"MarbleScape {VERSION}")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="TOML configuration path (default: marblescape_config.toml).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Perform one image update and exit.",
    )
    parser.add_argument(
        "--list-layers",
        nargs="?",
        const="",
        metavar="SEARCH",
        help="List layers or products for the selected source, then exit.",
    )
    parser.add_argument(
        "--export-layers",
        type=Path,
        metavar="FILE",
        help="Export layers or products for the selected source as JSON, then exit.",
    )
    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Validate configuration against the selected live source, then exit.",
    )
    parser.add_argument(
        "--print-urls",
        action="store_true",
        help="Print the effective endpoint or image URLs for the selected source, then exit.",
    )
    return parser.parse_args(argv)


def prepare_runtime_render_plan(layers):
    """Build a complete render plan from the currently loaded settings."""
    if IMAGE_SOURCE in {"goes_east", "goes_west", "solar"}:
        width, height = get_output_dimensions()
        return (width, height, width, height, 1.0, None, SOURCE_LABELS[IMAGE_SOURCE],
                None, [], "noaa", [], True, None)
    if IMAGE_SOURCE == "himawari":
        width, height = get_output_dimensions()
        return (width, height, width, height, 1.0, None, SOURCE_LABELS[IMAGE_SOURCE],
                None, [], "himawari", [], True, None)
    if IMAGE_SOURCE == "slider":
        width, height = get_output_dimensions()
        return (width, height, width, height, 1.0, None, SOURCE_LABELS[IMAGE_SOURCE],
                None, [], "slider", [], True, None)
    if IMAGE_SOURCE == "worldview":
        width, height = get_output_dimensions()
        return (width, height, width, height, 1.0, None, SOURCE_LABELS[IMAGE_SOURCE],
                None, [], "worldview", [], True, None)
    if IMAGE_SOURCE == "copernicus":
        width, height = get_output_dimensions()
        return (width, height, width, height, 1.0, None, SOURCE_LABELS[IMAGE_SOURCE],
                None, [], "copernicus", [], True, None)
    target_ratio = parse_aspect_ratio(ASPECT_RATIO)
    output_width, output_height = get_output_dimensions()
    render_width, render_height, effective_render_scale = get_render_dimensions(
        output_width,
        output_height,
    )
    projection_name, projection, base_extent = get_active_view()
    bbox, _ = calculate_bbox(projection, target_ratio, base_extent)
    resolved_layers = resolve_configured_layers(layers, projection)
    render_mode, requests = build_render_plan(
        resolved_layers,
        projection,
        bbox,
        render_width,
        render_height,
    )
    refresh_latest_times = any(
        layer["uses_latest_time"] for layer in resolved_layers
    )
    time_signature = tuple(
        (layer["name"], layer["time"])
        for layer in resolved_layers
        if layer["uses_latest_time"]
    )
    return (
        output_width,
        output_height,
        render_width,
        render_height,
        effective_render_scale,
        bbox,
        projection_name,
        projection,
        resolved_layers,
        render_mode,
        requests,
        refresh_latest_times,
        time_signature,
    )


def image_configuration_key(configuration):
    """Compare settings that change the downloaded/rendered image, not Windows placement."""
    source = configuration["IMAGE_SOURCE"]
    common = ["WIDTH", "HEIGHT", "ASPECT_RATIO", "CUSTOM_LATEST_FOLDER",
              "OUTPUT_ROOT_WINDOWS", "OUTPUT_ROOT_LINUX"]
    if source != "copernicus":
        common += ["BACKGROUND_COLOR", "VIEW_MODE", "ZOOM"]
    key = {name: configuration[name] for name in common}
    key["source"] = source
    if source == "eumetsat":
        for name in ("WMS_URL", "WMS_VERSION", "IMAGE_TIME", "RENDER_MODE", "VIEW_PRESET",
                     "TRUECOLOR_BLACK_NIGHT", "RENDER_SCALE", "RENDER_SCALE_AUTOMATIC", "LAYER_CONFIG"):
            key[name] = configuration[name]
        preset = VIEW_PRESETS[configuration["VIEW_PRESET"]]
        key["projection"] = preset["projection"] or configuration["PROJECTION"]
        key["bbox"] = configuration["CUSTOM_BBOX"] if configuration["VIEW_PRESET"] == "custom" else None
        profile = configuration["SOURCE_PROFILES"]["eumetsat"]
        key["gap_fill"] = {
            "enabled": profile.get("fill_gaps", False),
            "lookback_hours": profile.get("gap_fill_lookback_hours", 12),
        }
    else:
        key["selection"] = configuration["SOURCE_PROFILES"][source]
        if source == "copernicus":
            key["copernicus_client_id"] = configuration["COPERNICUS_CLIENT_ID"]
            key["copernicus_secret_fingerprint"] = hashlib.sha256(
                configuration["COPERNICUS_CLIENT_SECRET"].encode("utf-8")
            ).hexdigest()
    return deepcopy(key)


def image_cache_configuration_key(configuration):
    """Return only render-affecting values for persistent profile reuse."""
    key = image_configuration_key(configuration)
    for name in (
        "CUSTOM_LATEST_FOLDER", "OUTPUT_ROOT_WINDOWS", "OUTPUT_ROOT_LINUX"
    ):
        key.pop(name, None)
    return key


def main(argv=None, configuration_loaded=False, status_callback=None):
    global RUN_CONTINUOUSLY, NEXT_ROTATION_DEADLINE

    args = parse_arguments(argv)
    if not configuration_loaded:
        load_configuration(args.config)
    if args.once:
        RUN_CONTINUOUSLY = False

    validate_configuration()
    if get_current_image_path() is None:
        existing_latest = get_latest_image_files()
        set_current_image_path(existing_latest[0] if existing_latest else None)
    diagnostic = (args.list_layers is not None or args.export_layers is not None
                  or args.print_urls or args.validate_config)
    if not diagnostic:
        synchronize_profile_image_cache()
    rotation = RotationScheduler(IMAGE_PROFILE_LIBRARY if RUN_CONTINUOUSLY and not diagnostic else {})
    rotation.set_max_attempts(DOWNLOAD_RETRIES + 1)
    rotating_at_start = rotation.due()
    pending_profile = None
    profile_previous = None
    profile_previous_image = None
    active_profile_id = None
    configuration_before_plan = None
    needs_plan = rotating_at_start
    NEXT_ROTATION_DEADLINE = None
    set_rotation_status("Starting rotation..." if rotating_at_start else "Rotation is disabled.",
                        active_profile_id=None)
    report_update_status(status_callback, "checking")

    if IMAGE_SOURCE == "copernicus" and (
        args.list_layers is not None or args.export_layers is not None
        or args.print_urls or args.validate_config
    ):
        run_copernicus_diagnostics(args)
        return

    if IMAGE_SOURCE in {"goes_east", "goes_west", "solar"} and (
        args.list_layers is not None or args.export_layers is not None
        or args.print_urls or args.validate_config
    ):
        run_noaa_diagnostics(args)
        return

    if IMAGE_SOURCE == "himawari" and (
        args.list_layers is not None or args.export_layers is not None
        or args.print_urls or args.validate_config
    ):
        run_himawari_diagnostics(args)
        return

    if IMAGE_SOURCE == "slider" and (
        args.list_layers is not None or args.export_layers is not None
        or args.print_urls or args.validate_config
    ):
        run_slider_diagnostics(args)
        return

    if IMAGE_SOURCE == "worldview" and (
        args.list_layers is not None or args.export_layers is not None
        or args.print_urls or args.validate_config
    ):
        run_worldview_diagnostics(args)
        return

    layers = {}
    initial_saved_image = None
    if (
        not diagnostic and not rotating_at_start
        and not CHECK_FOR_SOURCE_UPDATES and not FORCE_UPDATE_EVENT.is_set()
    ):
        initial_output_size = get_output_dimensions()
        initial_saved_image = reusable_latest_image(
            image_cache_configuration_key(capture_loaded_configuration()),
            initial_output_size,
        )
    if IMAGE_SOURCE == "eumetsat" and not rotating_at_start and initial_saved_image is None:
        capabilities_xml = download_capabilities()
        layers = parse_layers(capabilities_xml)
        log(f"Discovered {len(layers)} WMS layer(s).")

    if args.list_layers is not None:
        print_layer_catalog(layers, args.list_layers)
    if args.export_layers is not None:
        export_layer_catalog(layers, args.export_layers)
    if args.list_layers is not None or args.export_layers is not None:
        return

    (
        output_width,
        output_height,
        render_width,
        render_height,
        effective_render_scale,
        bbox,
        projection_name,
        projection,
        resolved_layers,
        render_mode,
        requests,
        refresh_latest_times,
        time_signature,
    ) = (
        (*initial_output_size, *initial_output_size, 1.0, None, "", None, [],
         "saved", [], False, ())
        if initial_saved_image is not None else
        prepare_runtime_render_plan(layers) if not rotating_at_start else
        (0, 0, 0, 0, 1.0, None, "", None, [], "noaa", [], True, None)
    )

    if not rotating_at_start and initial_saved_image is None:
        print_configuration(output_width, output_height, render_width, render_height,
                            effective_render_scale, bbox, projection_name, projection,
                            resolved_layers, render_mode)
    elif initial_saved_image is not None:
        log(
            "Image update checks are disabled; a verified matching local image "
            "is available."
        )

    if args.print_urls:
        for index, request_spec in enumerate(requests, start=1):
            print(f"URL {index} ({request_spec['label']}):")
            print(request_spec["url"])
        return

    if args.validate_config:
        if (
            render_mode in {"local", "truecolor_black_night"}
            or (render_width, render_height) != (output_width, output_height)
        ):
            try:
                import PIL  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    "Configuration needs local composition. Install Pillow with "
                    "'python -m pip install -r requirements.txt'."
                ) from exc
        log("Configuration is valid against the live WMS capabilities.")
        return

    if not rotating_at_start:
        ensure_directories()

    storage_estimate_printed = False
    wallpaper_applied_path = None
    wallpaper_position_pending = False
    first_cycle = True
    last_image_cycle_started = None
    defer_image_check_until = None
    while True:
        if APPLICATION_STOP_EVENT.is_set():
            break

        if CONFIGURATION_RELOAD_EVENT.is_set():
            CONFIGURATION_RELOAD_EVENT.clear()
            previous_configuration = capture_loaded_configuration()
            try:
                load_configuration(args.config)
                validate_configuration()
                rotation.set_max_attempts(DOWNLOAD_RETRIES + 1)
                synchronize_profile_image_cache()
                image_changed = image_configuration_key(previous_configuration) != image_configuration_key(capture_loaded_configuration())
                rotation_changed = rotation.configure(IMAGE_PROFILE_LIBRARY if RUN_CONTINUOUSLY else {})
                if rotation_changed:
                    pending_profile = None
                    profile_previous = None
                    profile_previous_image = None
                    active_profile_id = None
                    set_rotation_status("Starting rotation..." if rotation.due() else "Rotation is disabled.",
                                        active_profile_id=None)
                elif image_changed:
                    active_profile_id = None
                    set_rotation_status("Waiting for the next rotation interval.",
                                        rotation.deadline, active_profile_id=None)
                if image_changed:
                    configuration_before_plan = previous_configuration
                    needs_plan = True
                    storage_estimate_printed = False
                    wallpaper_applied_path = None
                    first_cycle = True
                    defer_image_check_until = None
                elif last_image_cycle_started is not None:
                    # Wake to apply local settings, then resume the existing
                    # download/retry schedule without checking any image URLs.
                    defer_image_check_until = (
                        NEXT_ROTATION_DEADLINE if pending_profile is not None and NEXT_ROTATION_DEADLINE is not None
                        else last_image_cycle_started + UPDATE_INTERVAL_MINUTES * 60.0
                    )
                wallpaper_position_pending = (
                    SET_WINDOWS_WALLPAPER and (
                        WINDOWS_WALLPAPER_POSITION != previous_configuration["WINDOWS_WALLPAPER_POSITION"]
                        or WINDOWS_WALLPAPER_MONITOR_POSITIONS != previous_configuration[
                            "WINDOWS_WALLPAPER_MONITOR_POSITIONS"]
                        or WINDOWS_WALLPAPER_MONITOR_OUTPUTS != previous_configuration[
                            "WINDOWS_WALLPAPER_MONITOR_OUTPUTS"]
                        or not previous_configuration["SET_WINDOWS_WALLPAPER"]
                    )
                )
                log("Applied configuration without restarting MarbleScape.")
                if image_changed:
                    with IMAGE_STATUS_LOCK:
                        IMAGE_STATUS["error"] = ""
            except Exception as exc:
                restore_loaded_configuration(previous_configuration)
                rotation.set_max_attempts(DOWNLOAD_RETRIES + 1)
                log(f"Unable to apply configuration: {exc}")

        cycle_started = time.monotonic()
        force_download = FORCE_UPDATE_EVENT.is_set()
        FORCE_UPDATE_EVENT.clear()
        cycle_next_check = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            minutes=UPDATE_INTERVAL_MINUTES
        )
        report_update_status(status_callback, "checking", cycle_next_check)

        # Windows placement is independent of downloading a replacement image.
        # A source outage must not prevent positioning the existing wallpaper.
        if wallpaper_position_pending and os.name == "nt" and SET_WINDOWS_WALLPAPER:
            try:
                current_path = get_current_image_path()
                if current_path is None:
                    current_images = get_latest_image_files()
                    current_path = current_images[0] if current_images else None
                if current_path is not None:
                    set_windows_wallpaper(current_path)
                    wallpaper_applied_path = current_path
                    wallpaper_position_pending = False
            except Exception as exc:
                log(f"Windows wallpaper position warning: {exc}")

        if defer_image_check_until is not None:
            if not force_download and not rotation.due() and time.monotonic() < defer_image_check_until:
                NEXT_ROTATION_DEADLINE = defer_image_check_until
                if pending_profile is None and rotation.deadline is not None:
                    NEXT_ROTATION_DEADLINE = min(NEXT_ROTATION_DEADLINE, rotation.deadline)
                if wallpaper_position_pending:
                    NEXT_ROTATION_DEADLINE = min(NEXT_ROTATION_DEADLINE, time.monotonic() + 5)
                if not sleep_until_next_cycle(last_image_cycle_started, status_callback):
                    break
                continue
            defer_image_check_until = None

        last_image_cycle_started = cycle_started

        if pending_profile is None and rotation.due():
            pending_profile = rotation.start_next()
            profile_previous = image_settings_snapshot()
            profile_previous_image = (
                get_current_image_path(), get_active_profile_cache_id()
            )

        try:
            if pending_profile is not None:
                set_rotation_status(
                    f"Loading {pending_profile['name']} "
                    f"(attempt {rotation.attempts + 1}/{rotation.max_attempts})..."
                )
                apply_image_settings(pending_profile["settings"])
                needs_plan = True

            cache_profile_id = (
                pending_profile["id"] if pending_profile is not None else active_profile_id
            )
            cache_configuration_signature = image_cache_configuration_key(
                capture_loaded_configuration()
            )
            frozen_path = None
            if not CHECK_FOR_SOURCE_UPDATES and not force_download:
                frozen_output_size = get_output_dimensions()
                try:
                    if cache_profile_id is None:
                        frozen_path = reusable_latest_image(
                            cache_configuration_signature,
                            frozen_output_size,
                        )
                    else:
                        frozen_path = get_profile_cache().lookup_configuration(
                            cache_profile_id,
                            cache_configuration_signature,
                            frozen_output_size,
                        )
                except Exception as exc:
                    log(f"Saved image lookup warning: {exc}")

            if frozen_path is not None:
                output_width, output_height = frozen_output_size
                render_width, render_height = frozen_output_size
                render_mode, requests = "saved", []
                refresh_latest_times, time_signature = False, ()
                ensure_directories()
                configuration_before_plan = None
                # Keep the plan pending so a later manual force can resolve and
                # contact the selected provider normally.
                needs_plan = True
            elif needs_plan:
                if IMAGE_SOURCE == "eumetsat":
                    layers = parse_layers(download_capabilities())
                (output_width, output_height, render_width, render_height,
                 effective_render_scale, bbox, projection_name, projection, resolved_layers,
                 render_mode, requests, refresh_latest_times, time_signature) = prepare_runtime_render_plan(layers)
                ensure_directories()
                first_cycle = True
                if pending_profile is not None:
                    wallpaper_applied_path = None
                storage_estimate_printed = False
                needs_plan = False
                configuration_before_plan = None
                print_configuration(output_width, output_height, render_width, render_height,
                                    effective_render_scale, bbox, projection_name, projection,
                                    resolved_layers, render_mode)
            should_download = True
            pending_signature = time_signature

            if frozen_path is not None:
                should_download = False
                first_cycle = False
                log(
                    "Image update checks are disabled; reusing the saved image: "
                    f"{frozen_path}"
                )
            elif render_mode == "noaa":
                frame = get_noaa_frame((output_width, output_height))
                pending_signature = noaa_frame_signature(frame)
                should_download = first_cycle or force_download or pending_signature != time_signature
                if time_signature and pending_signature[:4] == time_signature[:4]:
                    pending_time = dt.datetime.fromisoformat(pending_signature[4].replace("Z", "+00:00"))
                    installed_time = dt.datetime.fromisoformat(time_signature[4].replace("Z", "+00:00"))
                    if pending_time < installed_time:
                        raise RuntimeError("NOAA currently lists an older image; keeping the newer installed image.")
                requests = [{"frame": frame}]
                if not should_download:
                    with IMAGE_STATUS_LOCK:
                        IMAGE_STATUS["error"] = ""
                    log("No newer image is available for the selected NOAA product and size.")
            elif render_mode == "himawari":
                frame = get_himawari_frame((output_width, output_height))
                pending_signature = himawari_frame_signature(frame)
                should_download = first_cycle or force_download or pending_signature != time_signature
                if time_signature and pending_signature[:4] == time_signature[:4]:
                    pending_time = dt.datetime.fromisoformat(pending_signature[4].replace("Z", "+00:00"))
                    installed_time = dt.datetime.fromisoformat(time_signature[4].replace("Z", "+00:00"))
                    if pending_time < installed_time:
                        raise RuntimeError(
                            "Himawari currently lists an older image; keeping the newer installed image."
                        )
                requests = [{"frame": frame}]
                if not should_download:
                    with IMAGE_STATUS_LOCK:
                        IMAGE_STATUS["error"] = ""
                    log("No newer Himawari image is available for the selected product and size.")
            elif render_mode == "slider":
                frame = get_slider_frame((output_width, output_height))
                pending_signature = slider_frame_signature(frame)
                should_download = first_cycle or force_download or pending_signature != time_signature
                if time_signature and pending_signature[:4] == time_signature[:4]:
                    pending_time = dt.datetime.fromisoformat(pending_signature[4].replace("Z", "+00:00"))
                    installed_time = dt.datetime.fromisoformat(time_signature[4].replace("Z", "+00:00"))
                    if pending_time < installed_time:
                        raise RuntimeError(
                            "CIRA SLIDER currently lists an older image; keeping the newer installed image."
                        )
                requests = [{"frame": frame}]
                if not should_download:
                    with IMAGE_STATUS_LOCK:
                        IMAGE_STATUS["error"] = ""
                    log("No newer CIRA SLIDER image is available for the selected product and size.")
            elif render_mode == "worldview":
                frame = get_worldview_frame((output_width, output_height))
                pending_signature = worldview_frame_signature(frame)
                should_download = first_cycle or force_download or pending_signature != time_signature
                if (time_signature and pending_signature[:4] == time_signature[:4]
                        and pending_signature[4] != "timeless" and time_signature[4] != "timeless"):
                    pending_time = dt.datetime.fromisoformat(
                        pending_signature[4].replace("Z", "+00:00")
                    )
                    installed_time = dt.datetime.fromisoformat(
                        time_signature[4].replace("Z", "+00:00")
                    )
                    if pending_time < installed_time:
                        raise RuntimeError(
                            "NASA GIBS currently lists an older acquisition; "
                            "keeping the newer installed image."
                        )
                requests = [{"frame": frame}]
                if not should_download:
                    with IMAGE_STATUS_LOCK:
                        IMAGE_STATUS["error"] = ""
                    log("No newer NASA Worldview acquisition is available for the selected layer.")
            elif render_mode == "copernicus":
                frame = get_copernicus_frame((output_width, output_height))
                pending_signature = copernicus_frame_signature(frame)
                should_download = first_cycle or force_download or pending_signature != time_signature
                if (time_signature and pending_signature[:-1] == time_signature[:-1]
                        and pending_signature[-1] != "timeless" and time_signature[-1] != "timeless"):
                    pending_time = dt.datetime.fromisoformat(pending_signature[-1].replace("Z", "+00:00"))
                    installed_time = dt.datetime.fromisoformat(time_signature[-1].replace("Z", "+00:00"))
                    if pending_time < installed_time:
                        raise RuntimeError(
                            "Copernicus currently lists an older acquisition; "
                            "keeping the newer installed image."
                        )
                requests = [{"frame": frame}]
                if not should_download:
                    with IMAGE_STATUS_LOCK:
                        IMAGE_STATUS["error"] = ""
                    log("No newer Copernicus acquisition is available for the selected product and location.")
            elif not first_cycle and refresh_latest_times:
                refreshed_xml = download_capabilities()
                refreshed_catalog = parse_layers(refreshed_xml)
                refreshed_layers = resolve_configured_layers(
                    refreshed_catalog,
                    projection,
                    log_resolutions=False,
                )
                refreshed_mode, refreshed_requests = build_render_plan(
                    refreshed_layers,
                    projection,
                    bbox,
                    render_width,
                    render_height,
                )
                refreshed_signature = tuple(
                    (layer["name"], layer["time"])
                    for layer in refreshed_layers
                    if layer["uses_latest_time"]
                )

                if refreshed_signature == time_signature and not force_download:
                    should_download = False
                    log("No newer complete layer timestamp is available.")
                elif refreshed_signature == time_signature:
                    log("Downloading the current layer timestamp again on request.")
                else:
                    details = ", ".join(
                        f"{name}={layer_time}"
                        for name, layer_time in refreshed_signature
                    )
                    log(f"Latest complete layer time advanced: {details}")

                resolved_layers = refreshed_layers
                render_mode = refreshed_mode
                requests = refreshed_requests
                pending_signature = refreshed_signature
                refresh_latest_times = any(
                    layer["uses_latest_time"] for layer in resolved_layers
                )

            cached_profile_path = frozen_path
            cache_lookup_needed = should_download
            cache_source_time = (
                None if frozen_path is not None else
                profile_cache_source_time(render_mode, requests, pending_signature)
            )
            cached_latest_path = None
            if (
                cache_profile_id is None and frozen_path is None
                and should_download and not force_download
            ):
                try:
                    cached_latest_path = reusable_latest_image(
                        cache_configuration_signature,
                        (output_width, output_height),
                        source_signature=pending_signature,
                        source_time=cache_source_time,
                        allow_legacy_source_time=render_mode in {
                            "noaa", "himawari", "slider", "copernicus", "worldview"
                        },
                    )
                except Exception as exc:
                    log(f"Latest image lookup warning: {exc}")
                if cached_latest_path is not None:
                    try:
                        _write_latest_state(
                            cached_latest_path,
                            cache_configuration_signature,
                            (output_width, output_height),
                            pending_signature,
                            cache_source_time,
                        )
                    except Exception as exc:
                        log(f"Latest image state update warning: {exc}")
                    should_download = False
                    time_signature = pending_signature
                    first_cycle = False
                    record_source_frame_status(render_mode, requests)
                    log(
                        "No newer source image is available; reusing the "
                        f"verified latest image: {cached_latest_path}"
                    )
            if cache_profile_id is not None and frozen_path is None:
                if not force_download:
                    try:
                        cached_profile_path = get_profile_cache().lookup(
                            cache_profile_id,
                            cache_configuration_signature,
                            pending_signature,
                            (output_width, output_height),
                            source_time=cache_source_time,
                        )
                    except Exception as exc:
                        log(f"Profile cache lookup warning: {exc}")
                    if cached_profile_path is not None:
                        should_download = False
                        time_signature = pending_signature
                        first_cycle = False
                        record_source_frame_status(render_mode, requests)
                        if cache_lookup_needed:
                            log(f"Reusing cached profile image: {cached_profile_path}")
                    elif not should_download:
                        # The active cache entry was cleared, removed or damaged.
                        should_download = True

            if should_download:
                report_update_status(status_callback, "fetching", cycle_next_check)
                installed_path, current_path, downloaded_size = perform_update(
                    render_mode,
                    requests,
                    render_width,
                    render_height,
                    output_width,
                    output_height,
                    cache_profile_id=cache_profile_id,
                    cache_configuration_signature=cache_configuration_signature,
                    cache_source_signature=pending_signature,
                    cache_source_time=cache_source_time,
                )
                time_signature = pending_signature
                first_cycle = False
            elif cached_profile_path is not None:
                installed_path = None
                current_path = cached_profile_path
                downloaded_size = 0
            elif cached_latest_path is not None:
                installed_path = None
                current_path = cached_latest_path
                downloaded_size = 0
            else:
                installed_path = None
                latest_files = get_latest_image_files()
                current_path = latest_files[0] if latest_files else None
                downloaded_size = 0

            if current_path is not None:
                set_current_image_path(current_path, cache_profile_id)

            if should_download and not storage_estimate_printed:
                print_storage_estimate(downloaded_size)
                storage_estimate_printed = True

            if installed_path is not None:
                log("Status: new image installed.")
            else:
                log("Status: already up to date.")

            # Also apply the existing image once after startup or a settings
            # reload. If a transient Windows API error occurs, leaving this
            # value unchanged retries the operation during the next cycle.
            if (
                os.name == "nt"
                and SET_WINDOWS_WALLPAPER
                and current_path is not None
                and current_path != wallpaper_applied_path
                and not APPLICATION_STOP_EVENT.is_set()
                and not CONFIGURATION_RELOAD_EVENT.is_set()
            ):
                try:
                    set_windows_wallpaper(current_path)
                    wallpaper_applied_path = current_path
                    wallpaper_position_pending = False
                except Exception as exc:
                    if pending_profile is not None:
                        raise
                    log(f"Windows wallpaper update warning: {exc}")

            if pending_profile is not None:
                if CONFIGURATION_RELOAD_EVENT.is_set() or APPLICATION_STOP_EVENT.is_set():
                    raise RuntimeError("Profile update cancelled because configuration changed or the application is stopping.")
                rotation.success()
                active_profile_id = pending_profile["id"]
                set_rotation_status(f"Active profile: {pending_profile['name']}", rotation.deadline,
                                    active_profile_id=pending_profile["id"])
                pending_profile = None
                profile_previous = None
                profile_previous_image = None

        except DownloadCancelledError:
            log("Download cancelled by user; keeping the current wallpaper.")
            if render_mode in {"noaa", "himawari", "slider", "copernicus", "worldview"}:
                with IMAGE_STATUS_LOCK:
                    IMAGE_STATUS["error"] = ""
            if pending_profile is not None:
                name = pending_profile["name"]
                rotation.cancel()
                try:
                    apply_image_settings(profile_previous)
                except Exception as rollback_error:
                    log(f"Unable to restore the previous image selection: {rollback_error}")
                pending_profile = None
                profile_previous = None
                if profile_previous_image is not None:
                    set_current_image_path(*profile_previous_image)
                profile_previous_image = None
                needs_plan = True
                set_rotation_status(
                    f"Cancelled {name}; waiting for the next rotation interval.",
                    rotation.deadline,
                )

        except Exception as exc:
            log(f"Update error: {exc}")
            if pending_profile is None and configuration_before_plan is not None:
                restore_loaded_configuration(configuration_before_plan)
                rotation.configure(IMAGE_PROFILE_LIBRARY if RUN_CONTINUOUSLY else {})
                configuration_before_plan = None
                needs_plan = True
            if render_mode in {"noaa", "himawari", "slider", "copernicus", "worldview"} and not CONFIGURATION_RELOAD_EVENT.is_set():
                with IMAGE_STATUS_LOCK:
                    IMAGE_STATUS["error"] = str(exc)
            if args.once:
                raise
            if pending_profile is not None and not CONFIGURATION_RELOAD_EVENT.is_set() and not APPLICATION_STOP_EVENT.is_set():
                result = (rotation.failure(exhausted=True)
                          if isinstance(exc, DownloadRetriesExhausted)
                          else rotation.failure())
                name = pending_profile["name"]
                if result == "retry":
                    set_rotation_status(
                        f"{name}: attempt {rotation.attempts}/{rotation.max_attempts} failed; retrying..."
                    )
                else:
                    try:
                        apply_image_settings(profile_previous)
                    except Exception as rollback_error:
                        # A concurrent folder change can make the old Image
                        # snapshot incompatible with new History settings.
                        # Never roll back those newly saved General settings.
                        log(f"Unable to restore the previous image selection: {rollback_error}")
                    pending_profile = None
                    profile_previous = None
                    if profile_previous_image is not None:
                        set_current_image_path(*profile_previous_image)
                    profile_previous_image = None
                    needs_plan = True
                    set_rotation_status(
                        f"All profiles failed after {rotation.max_attempts} attempts each; "
                        "waiting for the next rotation interval."
                        if result == "wait" else
                        f"Skipped {name} after {rotation.max_attempts} failed attempts.",
                        rotation.deadline)

        if not RUN_CONTINUOUSLY:
            break

        # Profile intervals are independent of source image refresh intervals.
        # A short, interruptible retry delay avoids hammering unavailable servers.
        NEXT_ROTATION_DEADLINE = (time.monotonic() + 5 if pending_profile is not None
                                  else rotation.deadline)

        if not sleep_until_next_cycle(cycle_started, status_callback):
            log("Stop requested.")
            break


# =============================================================================
# WINDOWS NOTIFICATION AREA
# =============================================================================


WINDOWS_APP_USER_MODEL_ID = "Gittegatt.MarbleScape"


def set_windows_app_user_model_id():
    """Give every MarbleScape window the executable's stable taskbar identity."""
    if os.name != "nt":
        return False
    try:
        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            WINDOWS_APP_USER_MODEL_ID
        )
    except (AttributeError, OSError) as exc:
        log(f"Unable to set the Windows application identity: {exc}")
        return False
    if result != 0:
        log(f"Unable to set the Windows application identity: HRESULT 0x{result & 0xFFFFFFFF:08X}")
        return False
    return True


def apply_tk_window_icon(root):
    """Apply the MarbleScape sphere to a Tk title bar and taskbar window."""
    import tkinter as tk

    icon_directory = RESOURCE_DIR / "assets" / "icons"
    images = []
    for size in (256, 64, 48, 32, 16):
        path = icon_directory / f"marblescape_{size}.png"
        if not path.is_file():
            raise FileNotFoundError(f"Window icon asset not found: {path}")
        images.append(tk.PhotoImage(master=root, file=str(path)))
    root.iconphoto(True, *images)
    if os.name == "nt":
        ico_path = icon_directory / "marblescape.ico"
        if not ico_path.is_file():
            raise FileNotFoundError(f"Windows icon asset not found: {ico_path}")
        root.iconbitmap(default=str(ico_path))
    root._marblescape_window_icons = images


def create_windows_tray_image():
    from PIL import Image

    available_sizes = (16, 32, 48, 64, 128, 256, 512)
    requested_size = 16
    try:
        requested_size = max(
            ctypes.windll.user32.GetSystemMetrics(49),
            ctypes.windll.user32.GetSystemMetrics(50),
        )
    except (AttributeError, OSError):
        pass

    selected_size = next(
        (size for size in available_sizes if size >= requested_size),
        available_sizes[-1],
    )
    icon_path = (
        RESOURCE_DIR
        / "assets"
        / "icons"
        / f"marblescape_{selected_size}.png"
    )
    if not icon_path.is_file():
        raise FileNotFoundError(f"Tray icon asset not found: {icon_path}")

    with Image.open(icon_path) as image:
        return image.convert("RGBA").copy()


def should_use_windows_tray(argv=None):
    if os.name != "nt":
        return False

    arguments = list(sys.argv[1:] if argv is None else argv)
    non_tray_options = {
        "--version",
        "-h",
        "--help",
        "--once",
        "--list-layers",
        "--export-layers",
        "--validate-config",
        "--print-urls",
    }
    return not any(argument.split("=", 1)[0] in non_tray_options for argument in arguments)


def get_application_launch_arguments(include_current_arguments=False):
    if getattr(sys, "frozen", False):
        arguments = [str(Path(sys.executable).resolve())]
    else:
        launcher = Path(sys.executable).resolve()
        pythonw = launcher.with_name("pythonw.exe")
        if launcher.name.lower() == "python.exe" and pythonw.exists():
            launcher = pythonw
        arguments = [str(launcher), str(Path(__file__).resolve())]

    if include_current_arguments:
        arguments.extend(sys.argv[1:])

    return arguments


def get_windows_startup_command():
    """Start this installation with its active config, without transient CLI flags."""
    arguments = [*get_application_launch_arguments(), "--config",
                 str(resolve_script_relative_path(ACTIVE_CONFIG_PATH))]
    command = subprocess.list2cmdline(arguments)
    # HKCU Run command lines are limited to 260 Windows characters.
    if len(command.encode("utf-16-le")) // 2 > 260:
        raise ValueError(
            "The Windows startup command exceeds 260 characters. "
            "Use a shorter application or configuration path."
        )
    return command



def replace_toml_section_value(text, section_name, key, value):
    """Replace a TOML value or add the key to an existing section."""
    header_pattern = re.compile(
        rf"(?m)^\s*\[{re.escape(section_name)}\]\s*(?:#[^\r\n]*)?(?:\r?\n|$)"
    )
    header_match = header_pattern.search(text)
    if header_match is None:
        raise ValueError(f"TOML section [{section_name}] was not found.")

    next_section = re.search(r"(?m)^\s*\[", text[header_match.end() :])
    section_end = (
        header_match.end() + next_section.start()
        if next_section is not None
        else len(text)
    )
    section_text = text[header_match.end() : section_end]
    value_pattern = re.compile(
        rf"(?m)^(\s*{re.escape(key)}\s*=\s*)"
        rf"(\"(?:\\.|[^\"\\])*\"|'[^']*'|[^#\r\n]*?)"
        rf"(\s*(?:#.*)?)$"
    )
    value_match = value_pattern.search(section_text)
    if value_match is None:
        newline = "\r\n" if "\r\n" in text else "\n"
        leading_newline = "" if header_match.group(0).endswith(newline) else newline
        insertion = f"{leading_newline}{key} = {json.dumps(value)}{newline}"
        position = header_match.end()
        return text[:position] + insertion + text[position:]

    replacement = value_match.group(1) + json.dumps(value) + value_match.group(3)
    start = header_match.end() + value_match.start()
    end = header_match.end() + value_match.end()
    return text[:start] + replacement + text[end:]


def replace_toml_values(text, updates):
    """Replace multiple existing TOML values while preserving file formatting."""
    updated = text
    for section_name, key, value in updates:
        updated = replace_toml_section_value(updated, section_name, key, value)
    return updated


def ensure_display_configuration_section(text):
    """Add the display table when saving a configuration from an older version."""
    if re.search(r"(?m)^\s*\[display\]\s*(?:#[^\r\n]*)?$", text):
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.rstrip() + newline * 2 + "[display]" + newline


def ensure_download_configuration_section(text):
    """Add the download table when saving a configuration from an older version."""
    if re.search(r"(?m)^\s*\[download\]\s*(?:#[^\r\n]*)?$", text):
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.rstrip() + newline * 2 + "[download]" + newline


def ensure_profile_list_configuration_section(text):
    """Add the profile-list table when saving an older configuration."""
    if re.search(r"(?m)^\s*\[profile_list\]\s*(?:#[^\r\n]*)?$", text):
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.rstrip() + newline * 2 + "[profile_list]" + newline


def ensure_updates_configuration_section(text):
    if re.search(r"(?m)^\s*\[updates\]\s*(?:#[^\r\n]*)?$", text):
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.rstrip() + newline * 2 + "[updates]" + newline


def source_configuration_updates(provider, profiles, check_for_updates=None):
    provider, profiles = normalize_source_configuration(provider, profiles)
    updates = [("source", "provider", provider)]
    if check_for_updates is not None:
        if type(check_for_updates) is not bool:
            raise ValueError("Image update checking must be true or false.")
        updates.append(("source", "check_for_updates", check_for_updates))
    return updates + [
        (f"sources.{name}", field, value)
        for name, profile in profiles.items() for field, value in profile.items()
    ]


def replace_source_configuration(text, provider, profiles, check_for_updates=None):
    """Add source tables to older TOML files while retaining all existing settings."""
    newline = "\r\n" if "\r\n" in text else "\n"
    for section, key, value in source_configuration_updates(
        provider, profiles, check_for_updates
    ):
        if not re.search(rf"(?m)^\s*\[{re.escape(section)}\]\s*(?:#[^\r\n]*)?$", text):
            text = text.rstrip() + newline * 2 + f"[{section}]" + newline
        text = replace_toml_section_value(text, section, key, value)
    return text


def first_run_configuration_text(template_text):
    """Start every source with an automatic resolution on a fresh install."""
    text = template_text
    newline = "\r\n" if "\r\n" in text else "\n"
    for provider in AUTO_RESOLUTION_PROVIDERS:
        section = f"sources.{provider}"
        if not re.search(rf"(?m)^\s*\[{re.escape(section)}\]\s*(?:#[^\r\n]*)?$", text):
            text = text.rstrip() + newline * 2 + f"[{section}]" + newline
        text = replace_toml_section_value(text, section, "resolution", "auto")
    tomllib.loads(text)
    return text


def replace_copernicus_auth_configuration(text, auth):
    """Persist global Copernicus credentials outside rotatable image profiles."""
    if not isinstance(auth, dict):
        raise ValueError("Copernicus authentication settings are invalid.")
    client_id = str(auth.get("client_id", "")).strip()
    secret = str(auth.get("client_secret", ""))
    if len(client_id) > 500 or len(secret) > 4000:
        raise ValueError("Copernicus OAuth credentials are too long.")
    protected = (
        COPERNICUS_CLIENT_SECRET_PROTECTED
        if secret == COPERNICUS_CLIENT_SECRET
        else protect_client_secret(secret)
    )
    newline = "\r\n" if "\r\n" in text else "\n"
    if not re.search(r"(?m)^\s*\[copernicus\]\s*(?:#[^\r\n]*)?$", text):
        text = text.rstrip() + newline * 2 + "[copernicus]" + newline
    return replace_toml_values(text, (
        ("copernicus", "client_id", client_id),
        ("copernicus", "client_secret_protected", protected),
    ))


def current_history_max_age():
    return {
        "years": HISTORY_RETENTION_YEARS,
        "months": HISTORY_RETENTION_MONTHS,
        "days": HISTORY_RETENTION_DAYS,
        "hours": HISTORY_RETENTION_HOURS,
        "minutes": HISTORY_RETENTION_MINUTES,
    }


def projection_selection_updates(projection_name):
    """Select a projection without reusing a bbox from another CRS."""
    if projection_name not in PROJECTIONS:
        raise ValueError("Projection is invalid.")
    return (
        ("view", "projection", projection_name),
        ("view", "preset", "full_earth"),
        ("view", "zoom", DEFAULT_ZOOM),
        # Fitting a global geographic bbox to most screen ratios would extend
        # beyond +/-90 latitude. Crop keeps the request within valid bounds.
        ("view", "fit_mode", "crop" if projection_name == "Geographic" else "fit"),
    )


def preset_configuration_updates(preset_name):
    """Return the view settings belonging to a selectable preset profile."""
    if preset_name not in VIEW_PRESET_PROFILES:
        raise ValueError("View preset has no selectable profile.")
    profile = VIEW_PRESET_PROFILES[preset_name]
    return (
        ("view", "preset", preset_name),
        ("view", "projection", profile["projection"]),
        ("view", "fit_mode", profile["fit_mode"]),
        ("view", "zoom", profile["zoom"]),
    )


def apply_preset_to_configuration(text, preset_name):
    """Apply the complete preset profile to TOML configuration text."""
    profile = VIEW_PRESET_PROFILES[preset_name]
    updated = replace_toml_values(
        text,
        preset_configuration_updates(preset_name),
    )
    return replace_primary_wms_layer_name(
        updated,
        profile["satellite_layer"],
    )


def normalize_settings_form_values(values, provider=None):
    """Validate settings dialog text and return typed TOML updates."""
    is_wms = (provider or IMAGE_SOURCE) == "eumetsat"
    try:
        zoom = float(values["zoom"])
        width = int(values["width"])
        height = int(values["height"])
        update_interval = float(values["update_interval_minutes"])
        max_files = int(values["max_files"])
        years = int(values["years"])
        months = int(values["months"])
        days = int(values["days"])
        hours = int(values["hours"])
        minutes = int(values["minutes"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Numeric settings must contain valid numbers.") from exc

    render_scale = parse_render_scale_setting(values["render_scale"]) if is_wms else get_render_scale_setting()
    position = str(values["position"]).strip().lower()
    view_preset = str(values["view_preset"]).strip().lower() if is_wms else VIEW_PRESET
    projection_name = str(values.get("projection", PROJECTION)).strip() if is_wms else PROJECTION
    fit_mode = str(values["fit_mode"]).strip().lower()
    aspect_ratio = normalize_aspect_ratio_text(values["aspect_ratio"])
    background_color = normalize_background_color(values["background_color"])
    latest_folder = str(values.get("latest_folder", CUSTOM_LATEST_FOLDER)).strip()
    history_folder = str(values.get("history_folder", CUSTOM_HISTORY_FOLDER)).strip()
    validate_image_folders(
        resolve_script_relative_path(latest_folder)
        if latest_folder else OUTPUT_ROOT / CONTENT_DIRECTORY_NAME / LATEST_DIRECTORY_NAME,
        resolve_script_relative_path(history_folder)
        if history_folder else OUTPUT_ROOT / CONTENT_DIRECTORY_NAME / HISTORY_DIRECTORY_NAME,
    )
    retention_mode = str(values["retention_mode"]).strip().lower()
    display_time_zone = normalize_time_zone(values["time_zone"])
    download_speed_unit = normalize_download_speed_unit(values["download_speed_unit"])
    download_retries = normalize_download_retries(
        values.get("download_retries", DOWNLOAD_RETRIES)
    )
    catalogue_retries = normalize_catalogue_retries(
        values.get("catalogue_retries", CATALOGUE_RETRIES)
    )

    if position not in WALLPAPER_POSITION_CHOICES:
        raise ValueError("Wallpaper position is invalid.")
    monitor_positions = values.get("monitor_positions", WINDOWS_WALLPAPER_MONITOR_POSITIONS)
    if not isinstance(monitor_positions, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        or value not in WALLPAPER_POSITION_CHOICES
        for key, value in monitor_positions.items()
    ):
        raise ValueError("Monitor wallpaper positions are invalid.")
    monitor_outputs = normalize_monitor_output_settings(
        values.get("monitor_output_settings", WINDOWS_WALLPAPER_MONITOR_OUTPUTS),
        {"width": width, "height": height, "aspect_ratio": aspect_ratio,
         "render_scale": render_scale, "background_color": background_color},
    )
    if is_wms and view_preset not in VIEW_PRESETS:
        raise ValueError("View preset is invalid.")
    if is_wms and projection_name not in PROJECTIONS:
        raise ValueError("Projection is invalid.")
    # Existing regional presets remain geographic until projected presets
    # are implemented. Persist the effective projection shown in the UI.
    if is_wms:
        projection_name = VIEW_PRESETS[view_preset]["projection"] or projection_name
    if is_wms and projection_name not in available_projection_choices():
        raise ValueError("Projection is invalid.")
    if fit_mode not in {"fit", "crop"}:
        raise ValueError("Fit mode must be 'fit' or 'crop'.")
    if retention_mode not in {"count", "time", "both"}:
        raise ValueError("History retention mode is invalid.")
    if not math.isfinite(zoom) or zoom <= 0:
        raise ValueError("Zoom must be greater than zero.")
    if width <= 0:
        raise ValueError("Width must be greater than zero.")
    if height < 0:
        raise ValueError("Height must be zero or greater.")
    if not math.isfinite(update_interval) or update_interval <= 0:
        raise ValueError("Update interval must be greater than zero.")
    if max_files < 0:
        raise ValueError("Maximum history files cannot be negative.")

    ratio = parse_aspect_ratio(aspect_ratio)
    if height > 0:
        actual_ratio = width / height
        if abs(actual_ratio - ratio) / ratio > 0.005:
            raise ValueError(
                "Width and height do not match the aspect ratio. "
                "Set height to 0 for automatic calculation."
            )

    retention_values = (years, months, days, hours, minutes)
    if any(value < 0 for value in retention_values):
        raise ValueError("History age values cannot be negative.")
    if retention_mode in {"time", "both"} and not any(retention_values):
        raise ValueError(
            "Time-based history retention requires an age greater than zero."
        )

    updates = (
        ("windows", "set_wallpaper", bool(values["set_wallpaper"])),
        ("windows", "position", position),
        ("windows", "monitor_positions", json.dumps(monitor_positions, sort_keys=True)),
        ("windows", "monitor_output_settings", json.dumps(monitor_outputs, sort_keys=True)),
        ("display", "time_zone", display_time_zone),
        ("view", "preset", view_preset),
        ("view", "projection", projection_name),
        ("view", "fit_mode", fit_mode),
        ("view", "zoom", zoom),
        (
            "view",
            "truecolor_black_night",
            bool(values["truecolor_black_night"]),
        ),
        ("output", "width", width),
        ("output", "height", height),
        ("output", "aspect_ratio", aspect_ratio),
        ("output", "render_scale", render_scale),
        ("output", "background_color", background_color),
        ("output", "latest_folder", latest_folder),
        ("service", "update_interval_minutes", update_interval),
        ("download", "show_speed", bool(values["show_download_speed"])),
        ("download", "speed_unit", download_speed_unit),
        ("download", "retries", download_retries),
        ("download", "catalogue_retries", catalogue_retries),
        ("download", "show_progress", bool(values["show_download_progress"])),
        ("download", "show_progress_bar", bool(values["show_download_progress_bar"])),
        (
            "download", "keep_completed_visible",
            bool(values["keep_completed_download_visible"]),
        ),
        ("history", "enabled", bool(values["history_enabled"])),
        ("history", "folder", history_folder),
        ("history", "retention_mode", retention_mode),
        ("history", "max_files", max_files),
        ("history", "years", years),
        ("history", "months", months),
        ("history", "days", days),
        ("history", "hours", hours),
        ("history", "minutes", minutes),
    )
    if (provider or IMAGE_SOURCE) != "eumetsat":
        # Hidden WMS controls must never overwrite a retained EUMETSAT selection.
        wms_fields = {"preset", "projection", "truecolor_black_night"}
        updates = tuple(item for item in updates
                        if not (item[0] == "view" and item[1] in wms_fields)
                        and not (item[0] == "output" and item[1] == "render_scale"))
    return updates


def replace_primary_wms_layer_name(text, layer_name):
    block_pattern = re.compile(
        r"(?ms)^\s*\[\[layers\]\][^\r\n]*(?:\r?\n|$).*?"
        r"(?=^\s*\[\[layers\]\]|^\s*\[(?!\[)|\Z)"
    )
    name_pattern = re.compile(
        r'(?m)^(\s*name\s*=\s*)([^#\r\n]*?)(\s*(?:#.*)?)$'
    )

    for block_match in block_pattern.finditer(text):
        block = block_match.group(0)
        try:
            parsed = tomllib.loads(block)
        except tomllib.TOMLDecodeError:
            continue

        layers = parsed.get("layers", [])
        if not layers:
            continue
        layer = layers[0]
        if layer.get("kind") != "wms" or not layer.get("enabled", True):
            continue

        name_match = name_pattern.search(block)
        if name_match is None:
            raise ValueError("The enabled WMS layer has no name setting.")
        replacement = name_match.group(1) + json.dumps(layer_name) + name_match.group(3)
        updated_block = block[: name_match.start()] + replacement + block[name_match.end() :]
        return text[: block_match.start()] + updated_block + text[block_match.end() :]

    raise ValueError("No enabled [[layers]] entry with kind='wms' was found.")


def update_active_configuration(transform):
    """Update the active TOML file without losing concurrent UI changes."""
    with CONFIGURATION_FILE_LOCK:
        return update_active_configuration_unlocked(transform)


def update_active_configuration_unlocked(transform):
    config_path = ACTIVE_CONFIG_PATH.resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with config_path.open("r", encoding="utf-8", newline="") as handle:
        original = handle.read()

    updated = transform(original)
    tomllib.loads(updated)
    if updated == original:
        return False

    temporary_path = config_path.with_name(
        f".{config_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary_path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(updated)
        os.replace(temporary_path, config_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    return True


def read_profile_library_file(path=None):
    """Read and validate the standalone profiles.toml document."""
    path = Path(path or ACTIVE_PROFILE_LIBRARY_PATH).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Profile file not found: {path}")
    if path.stat().st_size > MAX_BACKUP_CONFIGURATION_BYTES:
        raise ValueError("profiles.toml is too large.")
    try:
        text = path.read_text(encoding="utf-8-sig")
        parsed = tomllib.loads(text)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Could not read profiles.toml: {exc}") from exc
    if set(parsed) != {"image_profiles"}:
        raise ValueError(
            "profiles.toml must contain exactly one [image_profiles] document."
        )
    return normalize_library(parsed["image_profiles"])


def write_profile_library_file_unlocked(library, path=None):
    """Atomically write a validated standalone profile library."""
    path = Path(path or ACTIVE_PROFILE_LIBRARY_PATH).resolve()
    text = serialize_library(library)
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_BACKUP_CONFIGURATION_BYTES:
        raise ValueError("profiles.toml is too large.")
    if path.exists() and path.read_bytes() == encoded:
        return False
    if not path.parent.exists():
        raise FileNotFoundError(f"Profile folder not found: {path.parent}")
    temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary_path.write_bytes(encoded)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return True


def update_profile_library_file(library):
    with CONFIGURATION_FILE_LOCK:
        return write_profile_library_file_unlocked(library)


def update_active_configuration_and_profiles(transform, library):
    """Commit settings and profiles together, rolling profiles back on failure."""
    library = normalize_library(library)
    with CONFIGURATION_FILE_LOCK:
        config_path = ACTIVE_CONFIG_PATH.resolve()
        profile_path = ACTIVE_PROFILE_LIBRARY_PATH.resolve()
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        with config_path.open("r", encoding="utf-8", newline="") as handle:
            original_config = handle.read()
        updated_config = transform(original_config)
        tomllib.loads(updated_config)
        updated_profiles = serialize_library(library)
        original_profile_bytes = (
            profile_path.read_bytes() if profile_path.exists() else None
        )
        profile_bytes = updated_profiles.encode("utf-8")
        if len(profile_bytes) > MAX_BACKUP_CONFIGURATION_BYTES:
            raise ValueError("profiles.toml is too large.")
        config_changed = updated_config != original_config
        profiles_changed = original_profile_bytes != profile_bytes
        if not config_changed and not profiles_changed:
            return False

        config_temp = config_path.with_name(
            f".{config_path.name}.{uuid.uuid4().hex}.tmp"
        )
        profile_temp = profile_path.with_name(
            f".{profile_path.name}.{uuid.uuid4().hex}.tmp"
        )
        profile_replaced = False
        try:
            if config_changed:
                config_temp.write_text(updated_config, encoding="utf-8", newline="")
            if profiles_changed:
                profile_temp.write_bytes(profile_bytes)
                os.replace(profile_temp, profile_path)
                profile_replaced = True
            if config_changed:
                os.replace(config_temp, config_path)
        except Exception as exc:
            if profile_replaced:
                try:
                    if original_profile_bytes is None:
                        profile_path.unlink(missing_ok=True)
                    else:
                        rollback = profile_path.with_name(
                            f".{profile_path.name}.{uuid.uuid4().hex}.rollback"
                        )
                        try:
                            rollback.write_bytes(original_profile_bytes)
                            os.replace(rollback, profile_path)
                        finally:
                            rollback.unlink(missing_ok=True)
                except Exception as rollback_error:
                    raise RuntimeError(
                        f"{exc} Profile rollback also failed: {rollback_error}"
                    ) from exc
            raise
        finally:
            config_temp.unlink(missing_ok=True)
            profile_temp.unlink(missing_ok=True)
        return True


def make_json_compatible(value):
    """Convert TOML values to a human-readable JSON-compatible snapshot."""
    if isinstance(value, dict):
        return {
            str(key): make_json_compatible(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [make_json_compatible(item) for item in value]
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    return value


def create_settings_backup_payload():
    """Return a lossless JSON backup of settings and profiles.toml."""
    with CONFIGURATION_FILE_LOCK:
        config_path = ACTIVE_CONFIG_PATH.resolve()
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        with config_path.open("r", encoding="utf-8", newline="") as handle:
            config_text = handle.read()
        parsed_settings = tomllib.loads(config_text)
        if ACTIVE_PROFILE_LIBRARY_PATH.exists():
            profile_library = read_profile_library_file()
        else:
            profile_library = normalize_library(IMAGE_PROFILE_LIBRARY)
        profiles_text = serialize_library(profile_library)
    config_digest = hashlib.sha256(config_text.encode("utf-8")).hexdigest()
    profiles_digest = hashlib.sha256(profiles_text.encode("utf-8")).hexdigest()
    created_at = (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    return {
        "format": SETTINGS_BACKUP_FORMAT,
        "version": SETTINGS_BACKUP_VERSION,
        "created_at_utc": created_at,
        "configuration_sha256": config_digest,
        "settings": make_json_compatible(parsed_settings),
        "configuration_toml": config_text,
        "profiles_sha256": profiles_digest,
        "profiles": make_json_compatible(profile_library),
        "profiles_toml": profiles_text,
        "windows_startup_enabled": is_windows_startup_enabled(),
    }


def parse_settings_backup_payload(payload):
    """Validate a JSON settings backup and return its restorable values."""
    if not isinstance(payload, dict):
        raise ValueError("The backup root must be a JSON object.")
    if payload.get("format") != SETTINGS_BACKUP_FORMAT:
        raise ValueError("This is not a MarbleScape settings backup.")
    version = payload.get("version")
    if version != SETTINGS_BACKUP_VERSION:
        raise ValueError(
            f"Unsupported backup version: {version!r}."
        )

    config_text = payload.get("configuration_toml")
    if not isinstance(config_text, str) or not config_text.strip():
        raise ValueError("The backup contains no TOML configuration.")
    if len(config_text.encode("utf-8")) > MAX_BACKUP_CONFIGURATION_BYTES:
        raise ValueError("The configuration stored in the backup is too large.")

    expected_digest = payload.get("configuration_sha256")
    actual_digest = hashlib.sha256(config_text.encode("utf-8")).hexdigest()
    if (
        not isinstance(expected_digest, str)
        or expected_digest.lower() != actual_digest
    ):
        raise ValueError("The backup configuration checksum is invalid.")

    try:
        parsed_settings = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"The backup contains invalid TOML: {exc}") from exc

    settings_snapshot = payload.get("settings")
    if settings_snapshot != make_json_compatible(parsed_settings):
        raise ValueError(
            "The readable settings snapshot does not match the configuration."
        )

    profiles_text = payload.get("profiles_toml")
    if not isinstance(profiles_text, str) or not profiles_text.strip():
        raise ValueError("The backup contains no profiles.toml document.")
    if len(profiles_text.encode("utf-8")) > MAX_BACKUP_CONFIGURATION_BYTES:
        raise ValueError("The profile library stored in the backup is too large.")
    expected_profiles_digest = payload.get("profiles_sha256")
    actual_profiles_digest = hashlib.sha256(
        profiles_text.encode("utf-8")
    ).hexdigest()
    if (
        not isinstance(expected_profiles_digest, str)
        or expected_profiles_digest.lower() != actual_profiles_digest
    ):
        raise ValueError("The backup profile-library checksum is invalid.")
    try:
        parsed_profiles = tomllib.loads(profiles_text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"The backup contains invalid profile TOML: {exc}") from exc
    if set(parsed_profiles) != {"image_profiles"}:
        raise ValueError("The backup profile document is invalid.")
    profile_library = normalize_library(parsed_profiles["image_profiles"])
    if payload.get("profiles") != make_json_compatible(profile_library):
        raise ValueError(
            "The readable profile snapshot does not match profiles.toml."
        )

    startup_enabled = payload.get("windows_startup_enabled")
    if not isinstance(startup_enabled, bool):
        raise ValueError("The backup contains an invalid Windows startup setting.")

    return config_text, profile_library, startup_enabled


def export_settings_backup(output_path):
    """Write the active settings to an atomic JSON backup file."""
    output_path = Path(output_path)
    if output_path.suffix.lower() != ".json":
        output_path = output_path.with_suffix(".json")
    output_path = output_path.resolve()
    if not output_path.parent.exists():
        raise FileNotFoundError(
            f"Backup folder does not exist: {output_path.parent}"
        )

    payload = create_settings_backup_payload()
    temporary_path = output_path.with_name(
        f".{output_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    return output_path


def import_settings_backup(input_path):
    """Read and validate a JSON settings backup without changing live state."""
    input_path = Path(input_path).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Backup file not found: {input_path}")
    if input_path.stat().st_size > MAX_SETTINGS_BACKUP_FILE_BYTES:
        raise ValueError("The backup file is too large.")

    try:
        payload = json.loads(input_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read the JSON backup: {exc}") from exc

    return parse_settings_backup_payload(payload)


def get_selected_wms_layer_name():
    for entry in LAYER_CONFIG:
        if (
            str(entry.get("kind", "")).lower() == "wms"
            and entry.get("enabled", True)
            and float(entry.get("opacity", 1.0)) > 0
        ):
            return str(entry.get("name", ""))
    return None


def _parse_windows_startup_command(command):
    """Parse Windows quoting without executing the registry command."""
    if not isinstance(command, str) or not command.strip() or "\0" in command:
        return None
    if len(command.encode("utf-16-le")) // 2 > 260:
        return None
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    parse = shell32.CommandLineToArgvW
    parse.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int))
    parse.restype = ctypes.POINTER(ctypes.c_wchar_p)
    free = kernel32.LocalFree
    free.argtypes = (ctypes.c_void_p,)
    free.restype = ctypes.c_void_p
    count = ctypes.c_int()
    pointer = parse(command, ctypes.byref(count))
    if not pointer:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [pointer[index] for index in range(count.value)]
    finally:
        free(pointer)


def _windows_startup_identity(snapshot):
    """Return (application path, config path, interpreter/launcher path) for a plain app launch only."""
    if snapshot is None:
        return None
    import winreg
    command, kind = snapshot
    if kind == winreg.REG_EXPAND_SZ and isinstance(command, str):
        command = winreg.ExpandEnvironmentStrings(command)
    elif kind != winreg.REG_SZ:
        return None
    arguments = _parse_windows_startup_command(command)
    if not arguments or not Path(arguments[0]).is_absolute():
        return None
    launcher = Path(arguments[0])
    if (launcher.name.casefold() in {"python.exe", "pythonw.exe"} or
            (not getattr(sys, "frozen", False) and
             launcher.name.casefold() == Path(sys.executable).name.casefold())):
        if len(arguments) < 2 or not Path(arguments[1]).is_absolute():
            return None
        application = Path(arguments[1])
        if application.suffix.casefold() != ".py":
            return None
        remaining = arguments[2:]
    else:
        if launcher.suffix.casefold() != ".exe":
            return None
        application = launcher
        remaining = arguments[1:]
    if not remaining:
        config_path = DEFAULT_CONFIG_PATH
    elif len(remaining) == 2 and remaining[0] == "--config" and remaining[1]:
        config_path = remaining[1]
    elif len(remaining) == 1 and remaining[0].startswith("--config=") and remaining[0][9:]:
        config_path = remaining[0][9:]
    else:
        # --once, diagnostics, unrelated arguments, and shell wrappers are not
        # a persistent MarbleScape startup registration.
        return None
    return (os.path.normcase(str(application.resolve())),
            os.path.normcase(str(resolve_script_relative_path(config_path))),
            os.path.normcase(str(launcher.resolve())))


def _windows_startup_matches_current(snapshot):
    identity = _windows_startup_identity(snapshot)
    if identity is None:
        return False
    active_config = os.path.normcase(str(resolve_script_relative_path(ACTIVE_CONFIG_PATH)))
    if identity[1] != active_config:
        return False
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        expected = os.path.normcase(str(executable))
        return identity[0] == expected and identity[2] == expected
    launchers = {os.path.normcase(str(executable))}
    if executable.name.casefold() in {"python.exe", "pythonw.exe"}:
        launchers.update(os.path.normcase(str(executable.with_name(name)))
                         for name in ("python.exe", "pythonw.exe"))
    return (identity[0] == os.path.normcase(str(Path(__file__).resolve()))
            and identity[2] in launchers)



def capture_windows_startup_state():
    """Read exactly this Run value for a later lossless transaction rollback."""
    if os.name != "nt":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY,
                            access=winreg.KEY_READ) as key:
            value, kind = winreg.QueryValueEx(key, WINDOWS_RUN_VALUE_NAME)
            return (list(value) if isinstance(value, list) else value, kind)
    except FileNotFoundError:
        return None


def restore_windows_startup_state(snapshot):
    """Restore only MarbleScape's Run value, preserving its data and registry type."""
    if os.name != "nt":
        return
    import winreg
    if snapshot is not None and (not isinstance(snapshot, tuple) or len(snapshot) != 2):
        raise ValueError("Invalid Windows startup snapshot.")
    if capture_windows_startup_state() == snapshot:
        return
    if snapshot is None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY,
                                access=winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, WINDOWS_RUN_VALUE_NAME)
        except FileNotFoundError:
            pass
    else:
        value, kind = snapshot
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY,
                                access=winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, WINDOWS_RUN_VALUE_NAME, 0, kind, value)


def is_windows_startup_enabled():
    """Whether Run registers this installation and active configuration."""
    if os.name != "nt":
        return False
    return _windows_startup_matches_current(capture_windows_startup_state())


def set_windows_startup_enabled(enabled):
    if os.name != "nt":
        return
    import winreg
    previous = capture_windows_startup_state()
    if enabled:
        desired = (get_windows_startup_command(), winreg.REG_SZ)
    else:
        if not _windows_startup_matches_current(previous):
            return
        desired = None
    if previous == desired:
        return
    try:
        restore_windows_startup_state(desired)
    except OSError as error:
        try:
            restore_windows_startup_state(previous)
        except OSError as rollback_error:
            raise RuntimeError(
                f"{error} Windows startup rollback also failed: {rollback_error}"
            ) from error
        raise



def run_application(
    argv=None,
    pause_on_error=True,
    configuration_loaded=False,
    status_callback=None,
):
    exit_code = 0

    try:
        main(
            argv,
            configuration_loaded=configuration_loaded,
            status_callback=status_callback,
        )
    except KeyboardInterrupt:
        log("Stopped by user.")
        exit_code = 130
    except Exception as exc:
        log(f"Fatal error: {exc}")
        exit_code = 1
    finally:
        can_pause = sys.stdin is not None and getattr(sys.stdin, "isatty", lambda: False)()
        if (
            os.name == "nt"
            and pause_on_error
            and WINDOWS_PAUSE_ON_EXIT
            and exit_code != 0
            and can_pause
        ):
            try:
                input("\nPress Enter to exit...")
            except (EOFError, OSError):
                pass

    return exit_code


def run_with_windows_tray(argv=None):
    set_windows_app_user_model_id()
    try:
        import pystray
    except ImportError:
        log(
            "Windows tray icon unavailable. Install dependencies with "
            "'python -m pip install -r requirements.txt'."
        )
        return run_application(argv)

    try:
        tray_args = parse_arguments(argv)
        load_configuration(tray_args.config)
    except Exception as exc:
        log(f"Fatal error: {exc}")
        try:
            ctypes.windll.user32.MessageBoxW(
                None,
                str(exc),
                "MarbleScape configuration error",
                0x10,
            )
        except Exception:
            pass
        return 1

    APPLICATION_STOP_EVENT.clear()
    FORCE_UPDATE_EVENT.clear()
    CONFIGURATION_RELOAD_EVENT.clear()
    result = {"exit_code": 0}
    settings_dialog_lock = threading.Lock()
    settings_dialog_state = {"open": False}
    support_dialog_lock = threading.Lock()
    support_dialog_state = {"open": False}
    tray_status_lock = threading.Lock()
    tray_status = {
        "state": "starting",
        "next_check": None,
    }

    def create_tray_dialog_root(tk):
        """Close each Tk window on its own GUI thread when the tray exits."""
        root = tk.Tk()

        def close_when_stopping():
            if APPLICATION_STOP_EVENT.is_set():
                root.destroy()
            else:
                root.after(100, close_when_stopping)

        root.after(100, close_when_stopping)
        return root

    def get_tray_status_snapshot():
        with tray_status_lock:
            return tray_status["state"], tray_status["next_check"]

    def format_next_check_status(time_zone=None):
        state, next_check = get_tray_status_snapshot()
        if next_check is not None:
            return format_display_datetime(
                next_check,
                DISPLAY_TIME_ZONE if time_zone is None else time_zone,
                include_seconds=True,
            )
        if state == "checking":
            return "Checking now"
        return "Calculating"

    def open_output_folder(icon, item):
        del item
        try:
            folder = (
                get_profile_cache().images_dir
                if get_active_profile_cache_id() is not None else LATEST_DIR
            )
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder.resolve()))
        except Exception as exc:
            try:
                icon.notify(str(exc), "Unable to open image folder")
            except Exception:
                log(f"Unable to open image folder: {exc}")

    def exit_application(icon, item):
        del item
        APPLICATION_STOP_EVENT.set()
        icon.stop()

    def show_tray_error(icon, title, error):
        try:
            icon.notify(str(error), title)
        except Exception:
            log(f"{title}: {error}")

    def run_update_notice_dialog(release):
        import tkinter as tk
        from tkinter import messagebox, ttk

        root = create_tray_dialog_root(tk)
        apply_tk_window_icon(root)
        original_destroy = root.destroy

        def destroy_notice():
            icons = getattr(root, "_marblescape_window_icons", [])
            root._marblescape_window_icons = []
            icons.clear()
            original_destroy()

        root.destroy = destroy_notice
        root.protocol("WM_DELETE_WINDOW", destroy_notice)
        root.withdraw()
        root.title("MarbleScape update available")
        root.resizable(False, False)
        frame = ttk.Frame(root, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            frame, text="A new MarbleScape version is available.",
            font=("TkDefaultFont", 10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            frame,
            text=f"Installed: v{VERSION.lstrip('v')}    Latest: {release['latest']}",
        ).grid(row=1, column=0, columnspan=2, pady=(6, 12), sticky="w")

        def skip_version():
            global SKIPPED_UPDATE_VERSION
            try:
                update_active_configuration(
                    lambda text: replace_toml_section_value(
                        ensure_updates_configuration_section(text),
                        "updates", "skipped_version", release["latest"],
                    )
                )
                SKIPPED_UPDATE_VERSION = release["latest"]
            except Exception as exc:
                messagebox.showerror("Unable to skip this version", str(exc), parent=root)
                return
            root.destroy()

        def open_release():
            try:
                if not webbrowser.open(release["url"], new=2):
                    raise RuntimeError("The web browser could not be opened.")
            except Exception as exc:
                messagebox.showerror("Unable to open GitHub", str(exc), parent=root)
                return
            root.destroy()

        ttk.Button(frame, text="Skip this version", command=skip_version).grid(
            row=2, column=0, padx=(0, 8), sticky="ew"
        )
        ttk.Button(frame, text="Open GitHub", command=open_release).grid(
            row=2, column=1, sticky="ew"
        )
        root.update_idletasks()
        root.geometry(
            f"+{max(0, (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2)}"
            f"+{max(0, (root.winfo_screenheight() - root.winfo_reqheight()) // 2)}"
        )
        root.deiconify()
        root.attributes("-topmost", True)
        root.after(250, lambda: root.attributes("-topmost", False))
        root.mainloop()

    def check_for_startup_update():
        def worker():
            try:
                release = check_github_update()
            except Exception as exc:
                log(f"GitHub update check warning: {exc}")
                return
            if (APPLICATION_STOP_EVENT.is_set() or not
                    should_show_update_notification(release, SKIPPED_UPDATE_VERSION)):
                return
            try:
                run_update_notice_dialog(release)
            except Exception as exc:
                log(f"Unable to show update notice: {exc}")

        threading.Thread(
            target=worker, name="MarbleScapeStartupUpdate", daemon=True,
        ).start()

    def restart_from_tray(icon):
        try:
            restart_environment = os.environ.copy()
            if getattr(sys, "frozen", False):
                restart_environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            restart_environment["MARBLESCAPE_RESTART_WAIT"] = "1"

            subprocess.Popen(
                [
                    *get_application_launch_arguments(include_current_arguments=True),
                    "--config",
                    str(ACTIVE_CONFIG_PATH.resolve()),
                ],
                cwd=str(SCRIPT_DIR),
                close_fds=True,
                env=restart_environment,
            )
        except Exception as exc:
            show_tray_error(icon, "Unable to restart MarbleScape", exc)
            return False

        APPLICATION_STOP_EVENT.set()
        icon.stop()
        return True

    def restart_application(icon, item):
        del item
        restart_from_tray(icon)

    def open_project_url(url):
        allowed = {
            "https://github.com/Gittegatt/MarbleScape",
            "https://ko-fi.com/gittegatt",
            "https://buymeacoffee.com/gittegatt",
        }
        if url not in allowed:
            raise ValueError("Unsupported project link.")
        if not webbrowser.open(url, new=2):
            raise RuntimeError("The web browser could not be opened.")

    def run_support_dialog():
        import tkinter as tk
        from tkinter import messagebox, ttk
        from PIL import Image, ImageDraw, ImageTk

        root = create_tray_dialog_root(tk)
        apply_tk_window_icon(root)
        root.title("Support this project")
        root.resizable(False, False)
        root.attributes("-topmost", True)
        frame = ttk.Frame(root, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            frame,
            text="Support MarbleScape",
            font=("TkDefaultFont", 11, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(
            frame,
            text="Choose where you would like to support the project:",
        ).grid(row=1, column=0, sticky="w", pady=(0, 10))

        def monochrome_icon(kind, size=20):
            image = Image.new("RGBA", (size, size), (255, 255, 255, 0))
            draw = ImageDraw.Draw(image)
            if kind == "star":
                points = []
                for index in range(10):
                    angle = -math.pi / 2 + index * math.pi / 5
                    radius = size * (0.43 if index % 2 == 0 else 0.19)
                    points.append((size / 2 + math.cos(angle) * radius,
                                   size / 2 + math.sin(angle) * radius))
                draw.polygon(points, fill="black")
            else:
                draw.rectangle((3, 5, 14, 14), fill="black")
                draw.ellipse((12, 7, 18, 13), outline="black", width=2)
                draw.rectangle((5, 15, 16, 17), fill="black")
                draw.line((6, 2, 6, 4), fill="black", width=1)
                draw.line((10, 1, 10, 4), fill="black", width=1)
            return ImageTk.PhotoImage(image)

        icons = {"star": monochrome_icon("star"), "coffee": monochrome_icon("coffee")}

        def open_link(url):
            try:
                open_project_url(url)
            except Exception as exc:
                messagebox.showerror(
                    "Unable to open link", str(exc), parent=root
                )
            finally:
                root.attributes("-topmost", False)

        choices = (
            ("Star on GitHub", "star", "https://github.com/Gittegatt/MarbleScape"),
            ("Ko-fi", "coffee", "https://ko-fi.com/gittegatt"),
            ("Buy me a coffee", "coffee", "https://buymeacoffee.com/gittegatt"),
        )
        for row, (label, icon_name, url) in enumerate(choices, start=2):
            ttk.Button(
                frame,
                text=label,
                image=icons[icon_name],
                compound="left",
                command=lambda value=url: open_link(value),
                width=25,
            ).grid(row=row, column=0, pady=3, sticky="ew")
        root._support_icons = icons
        root.update_idletasks()
        root.geometry(
            f"+{max(0, (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2)}"
            f"+{max(0, (root.winfo_screenheight() - root.winfo_reqheight()) // 2)}"
        )
        root.mainloop()

    def open_support_dialog(icon, item):
        del item
        with support_dialog_lock:
            if support_dialog_state["open"]:
                return
            support_dialog_state["open"] = True

        def worker():
            try:
                run_support_dialog()
            except Exception as exc:
                show_tray_error(icon, "Unable to open project support", exc)
            finally:
                with support_dialog_lock:
                    support_dialog_state["open"] = False

        threading.Thread(
            target=worker, name="MarbleScapeSupport", daemon=True
        ).start()

    def values_match(current, expected):
        if (
            isinstance(current, (int, float))
            and not isinstance(current, bool)
            and isinstance(expected, (int, float))
            and not isinstance(expected, bool)
        ):
            return math.isclose(float(current), float(expected), rel_tol=1e-9)
        return current == expected

    def request_runtime_configuration_reload(icon):
        CONFIGURATION_RELOAD_EVENT.set()
        try:
            icon.update_menu()
        except Exception as exc:
            log(f"Tray menu refresh warning: {exc}")

    def apply_configuration_updates(icon, error_title, updates):
        try:
            changed = update_active_configuration(
                lambda text: replace_toml_values(text, updates)
            )
        except Exception as exc:
            show_tray_error(icon, error_title, exc)
            return False

        if changed:
            request_runtime_configuration_reload(icon)
        else:
            icon.update_menu()
        return changed

    def apply_settings_backup(icon, config_text, profile_library, startup_enabled):
        startup_before_import = is_windows_startup_enabled()
        startup_changed = startup_enabled != startup_before_import
        if startup_changed:
            startup_snapshot = capture_windows_startup_state()
            set_windows_startup_enabled(startup_enabled)

        try:
            changed = update_active_configuration_and_profiles(
                lambda _text: config_text, profile_library
            )
        except Exception as config_error:
            if startup_changed:
                try:
                    restore_windows_startup_state(startup_snapshot)
                except Exception as rollback_error:
                    raise RuntimeError(
                        f"{config_error} Windows startup rollback also "
                        f"failed: {rollback_error}"
                    ) from config_error
            raise

        if changed:
            request_runtime_configuration_reload(icon)
        else:
            icon.update_menu()
        return changed

    def make_setting_action(section_name, key, value, current_value, extra_updates=()):
        def select_setting(icon, item):
            del item
            if values_match(current_value(), value) and not extra_updates:
                return
            updates = ((section_name, key, value), *extra_updates)
            apply_configuration_updates(icon, "Unable to change setting", updates)

        return select_setting

    def make_setting_checked(value, current_value):
        def setting_checked(item):
            del item
            return values_match(current_value(), value)

        return setting_checked

    def make_boolean_toggle(section_name, key, current_value):
        def toggle_setting(icon, item):
            del item
            apply_configuration_updates(
                icon,
                "Unable to change setting",
                ((section_name, key, not bool(current_value())),),
            )

        return toggle_setting

    def make_projection_action(projection_name):
        def select_projection(icon, item):
            del item
            apply_configuration_updates(
                icon,
                "Unable to change projection",
                projection_selection_updates(projection_name),
            )

        return select_projection

    def make_preset_action(preset_name):
        def select_preset(icon, item):
            del item
            try:
                changed = update_active_configuration(
                    lambda text: apply_preset_to_configuration(text, preset_name)
                )
            except Exception as exc:
                show_tray_error(icon, "Unable to change view preset", exc)
                return
            if changed:
                request_runtime_configuration_reload(icon)

        return select_preset

    def make_preset_checked(preset_name):
        def preset_checked(item):
            del item
            return VIEW_PRESET == preset_name

        return preset_checked

    def make_layer_action(layer_name):
        def select_layer(icon, item):
            del item
            if get_selected_wms_layer_name() == layer_name:
                return
            try:
                changed = update_active_configuration(
                    lambda text: replace_primary_wms_layer_name(text, layer_name)
                )
            except Exception as exc:
                show_tray_error(icon, "Unable to change satellite layer", exc)
                return
            if changed:
                request_runtime_configuration_reload(icon)

        return select_layer

    def make_layer_checked(layer_name):
        def layer_checked(item):
            del item
            return get_selected_wms_layer_name() == layer_name

        return layer_checked

    def make_output_size_action(width, height):
        aspect_ratio = aspect_ratio_for_dimensions(width, height)
        return make_setting_action(
            "output",
            "width",
            width,
            lambda: WIDTH,
            extra_updates=(
                ("output", "height", 0),
                ("output", "aspect_ratio", aspect_ratio),
            ),
        )

    def make_output_size_checked(width, height):
        def output_size_checked(item):
            del item
            try:
                actual_width, actual_height = get_output_dimensions()
            except (TypeError, ValueError):
                return False
            return actual_width == width and actual_height == height

        return output_size_checked

    def make_aspect_ratio_action(aspect_ratio):
        return make_setting_action(
            "output",
            "aspect_ratio",
            aspect_ratio,
            lambda: ASPECT_RATIO,
            extra_updates=(("output", "height", 0),),
        )

    def make_aspect_ratio_checked(aspect_ratio):
        def aspect_ratio_checked(item):
            del item
            try:
                return math.isclose(
                    parse_aspect_ratio(ASPECT_RATIO),
                    parse_aspect_ratio(aspect_ratio),
                    rel_tol=1e-9,
                )
            except (TypeError, ValueError):
                return False

        return aspect_ratio_checked

    def make_history_age_action(age):
        updates = tuple(("history", key, value) for key, value in age.items())

        def select_history_age(icon, item):
            del item
            if current_history_max_age() == age:
                return
            apply_configuration_updates(icon, "Unable to change history age", updates)

        return select_history_age

    def make_history_age_checked(age):
        def history_age_checked(item):
            del item
            return current_history_max_age() == age

        return history_age_checked

    def run_settings_dialog(icon):
        import tkinter as tk
        from tkinter import colorchooser, filedialog, messagebox, ttk
        from marblescape_source_layout import SOURCE_COMBO_WIDTH, configure_source_columns
        from marblescape_source_settings import SourceSettings

        root = create_tray_dialog_root(tk)
        apply_tk_window_icon(root)
        root.withdraw()
        root.title("MarbleScape settings")
        root.resizable(True, True)

        def number_text(value):
            numeric = float(value)
            return str(int(numeric)) if numeric.is_integer() else str(numeric)

        def prepare_choice_lookup(choices, current_value, custom_label):
            label_to_value = dict(choices)
            current_label = next(
                (
                    label
                    for label, value in choices
                    if value == current_value
                ),
                None,
            )
            if current_label is None:
                current_label = custom_label
                label_to_value[current_label] = current_value
            return label_to_value, current_label

        selected_wms_layer = get_selected_wms_layer_name()
        saved_layer_state = {"value": selected_wms_layer}
        preset_label_to_value, current_preset_label = prepare_choice_lookup(
            VIEW_PRESET_MENU_CHOICES,
            VIEW_PRESET,
            "Custom (configured bounding box)",
        )
        layer_label_to_value, current_layer_label = prepare_choice_lookup(
            SATELLITE_LAYER_MENU_CHOICES,
            selected_wms_layer,
            (
                f"Custom layer ({selected_wms_layer})"
                if selected_wms_layer
                else "No enabled WMS layer"
            ),
        )
        resolution_label_to_size = {
            f"{group_label} | {label}": (width, height)
            for group_label, choices in OUTPUT_SIZE_MENU_GROUPS
            for label, width, height in choices
        }
        resolution_label_by_size = {
            size: label
            for label, size in resolution_label_to_size.items()
        }
        aspect_label_to_value = {
            f"{group_label} | {label}": aspect_ratio
            for group_label, choices in ASPECT_RATIO_MENU_GROUPS
            for label, aspect_ratio in choices
        }
        render_quality_label_to_value = dict(RENDER_QUALITY_MENU_CHOICES)
        current_output_size = get_output_dimensions()
        current_resolution_label = resolution_label_by_size.get(
            current_output_size,
            "Custom",
        )
        current_aspect_label = next(
            (
                label
                for label, aspect_ratio in aspect_label_to_value.items()
                if math.isclose(
                    parse_aspect_ratio(ASPECT_RATIO),
                    parse_aspect_ratio(aspect_ratio),
                    rel_tol=1e-9,
                )
            ),
            "Custom",
        )
        current_render_quality = get_render_scale_setting()
        current_render_quality_label = next(
            (
                label
                for label, value in RENDER_QUALITY_MENU_CHOICES
                if (
                    value == current_render_quality
                    if isinstance(value, str)
                    else (
                        not isinstance(current_render_quality, str)
                        and math.isclose(
                            float(value),
                            float(current_render_quality),
                            rel_tol=1e-9,
                        )
                    )
                )
            ),
            "Custom",
        )
        time_zone_label_to_value = dict(TIME_ZONE_MENU_CHOICES)
        current_time_zone_label = next(
            label for label, value in TIME_ZONE_MENU_CHOICES
            if value == DISPLAY_TIME_ZONE
        )

        variables = {
            "set_wallpaper": tk.BooleanVar(value=SET_WINDOWS_WALLPAPER),
            "position": tk.StringVar(value=WINDOWS_WALLPAPER_POSITION),
            "output_device": tk.StringVar(value="All monitors"),
            "start_with_windows": tk.BooleanVar(
                value=is_windows_startup_enabled()
            ),
            "time_zone": tk.StringVar(value=current_time_zone_label),
            "check_for_source_updates": tk.BooleanVar(
                value=CHECK_FOR_SOURCE_UPDATES
            ),
            "view_preset": tk.StringVar(value=current_preset_label),
            "projection": tk.StringVar(value=get_active_view()[0]),
            "satellite_layer": tk.StringVar(value=current_layer_label),
            "fit_mode": tk.StringVar(value=VIEW_MODE),
            "zoom": tk.StringVar(value=number_text(ZOOM)),
            "truecolor_black_night": tk.BooleanVar(
                value=TRUECOLOR_BLACK_NIGHT
            ),
            "width": tk.StringVar(value=str(WIDTH)),
            "height": tk.StringVar(value=str(0 if HEIGHT is None else HEIGHT)),
            "aspect_ratio": tk.StringVar(value=str(ASPECT_RATIO or aspect_ratio_for_dimensions(*current_output_size))),
            "resolution_preset": tk.StringVar(
                value=current_resolution_label
            ),
            "aspect_ratio_preset": tk.StringVar(
                value=current_aspect_label
            ),
            "render_quality_preset": tk.StringVar(
                value=current_render_quality_label
            ),
            "render_scale": tk.StringVar(
                value=(
                    "auto"
                    if RENDER_SCALE_AUTOMATIC
                    else number_text(RENDER_SCALE)
                )
            ),
            "background_color": tk.StringVar(
                value=normalize_background_color(BACKGROUND_COLOR)
            ),
            "update_interval_minutes": tk.StringVar(
                value=number_text(UPDATE_INTERVAL_MINUTES)
            ),
            "show_download_speed": tk.BooleanVar(value=SHOW_DOWNLOAD_SPEED),
            "download_speed_unit": tk.StringVar(
                value="Automatic" if DOWNLOAD_SPEED_UNIT == "automatic" else DOWNLOAD_SPEED_UNIT
            ),
            "download_retries": tk.StringVar(value=str(DOWNLOAD_RETRIES)),
            "catalogue_retries": tk.StringVar(value=str(CATALOGUE_RETRIES)),
            "show_download_progress": tk.BooleanVar(value=SHOW_DOWNLOAD_PROGRESS),
            "show_download_progress_bar": tk.BooleanVar(value=SHOW_DOWNLOAD_PROGRESS_BAR),
            "keep_completed_download_visible": tk.BooleanVar(
                value=KEEP_COMPLETED_DOWNLOAD_VISIBLE
            ),
            "history_enabled": tk.BooleanVar(value=ENABLE_HISTORY),
            "retention_mode": tk.StringVar(value=HISTORY_RETENTION_MODE),
            "max_files": tk.StringVar(value=str(HISTORY_MAX_FILES)),
            "years": tk.StringVar(value=str(HISTORY_RETENTION_YEARS)),
            "months": tk.StringVar(value=str(HISTORY_RETENTION_MONTHS)),
            "days": tk.StringVar(value=str(HISTORY_RETENTION_DAYS)),
            "hours": tk.StringVar(value=str(HISTORY_RETENTION_HOURS)),
            "minutes": tk.StringVar(value=str(HISTORY_RETENTION_MINUTES)),
        }
        status_variables = {
            "image_source": tk.StringVar(),
            "activity": tk.StringVar(),
            "latest_images": tk.StringVar(),
            "current_image_size": tk.StringVar(),
            "estimated_history_images": tk.StringVar(),
            "estimated_maximum_total": tk.StringVar(),
            "total_storage_estimate": tk.StringVar(),
            "currently_used_disk_space": tk.StringVar(),
            "profile_cache": tk.StringVar(),
            "cache_action": tk.StringVar(),
            "history_action": tk.StringVar(),
            "next_check": tk.StringVar(),
            "download": tk.StringVar(),
        }
        variables["latest_folder"] = tk.StringVar(value=CUSTOM_LATEST_FOLDER)
        variables["history_folder"] = tk.StringVar(value=CUSTOM_HISTORY_FOLDER)
        image_form_state = {"base": image_settings_snapshot(), "loaded": False}

        container = ttk.Frame(root, padding=12)
        container.grid(row=0, column=0, sticky="nsew")
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        notebook = ttk.Notebook(container)
        notebook.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        scroll_pages = {}

        def add_settings_tab(title):
            page = ttk.Frame(notebook)
            page.rowconfigure(0, weight=1)
            page.columnconfigure(0, weight=1)
            canvas = tk.Canvas(
                page, highlightthickness=0, borderwidth=0,
                background=ttk.Style(root).lookup("TFrame", "background") or "#f0f0f0",
            )
            canvas.grid(row=0, column=0, sticky="nsew")
            scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
            scrollbar.grid(row=0, column=1, sticky="ns")
            canvas.configure(yscrollcommand=scrollbar.set)
            content = ttk.Frame(canvas, padding=(0, 8, 8, 0))
            content.columnconfigure(0, weight=1)
            window = canvas.create_window(0, 0, window=content, anchor="nw")

            def update_scroll_region(_event=None):
                top = max(0, canvas.canvasy(0))
                width = max(1, canvas.winfo_width())
                height = content.winfo_reqheight()
                canvas.itemconfigure(window, width=width)
                canvas.configure(scrollregion=(0, 0, width, height))
                # Preserve the pixel offset as source fields and labels change.
                # Reserving the scrollbar avoids repeated width/reflow changes.
                top = min(top, max(0, height - canvas.winfo_height()))
                canvas.yview_moveto(top / max(1, height))

            content.bind("<Configure>", update_scroll_region)
            canvas.bind("<Configure>", update_scroll_region)
            notebook.add(page, text=title)
            scroll_pages[str(page)] = (canvas, content)
            return content

        general_tab = add_settings_tab("General")
        image_tab = add_settings_tab("Image")
        download_tab = add_settings_tab("Download")
        profiles_tab = add_settings_tab("Profiles & Rotation")
        history_tab = add_settings_tab("Storage & History")
        backup_tab = add_settings_tab("Backup")
        sources_tab = add_settings_tab("Sources")
        info_tab = add_settings_tab("Info")
        about_tab = add_settings_tab("About")

        def scroll_settings(event):
            selected = scroll_pages.get(notebook.select())
            if selected is None:
                return
            canvas, _content = selected
            # Only scroll the selected page, not the fixed footer or other windows.
            widget = event.widget
            while widget is not None and widget is not canvas:
                widget = getattr(widget, "master", None)
            if widget is None:
                return
            if getattr(event, "num", None) in (4, 5):
                units = -1 if event.num == 4 else 1
            else:
                delta = getattr(event, "delta", 0)
                if not delta:
                    return
                units = -int(delta / 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
            if isinstance(event.widget, ttk.Treeview) and event.widget.yview() != (0.0, 1.0):
                event.widget.yview_scroll(units * 3, "units")
                return "break"
            if canvas.yview() == (0.0, 1.0):
                return "break"
            canvas.yview_scroll(units * 3, "units")
            return "break"

        def reveal_focused_setting():
            selected = scroll_pages.get(notebook.select())
            if selected is None:
                return
            canvas, content = selected
            widget = root.focus_get()
            ancestor = widget
            while ancestor is not None and ancestor is not content:
                ancestor = getattr(ancestor, "master", None)
            if ancestor is None:
                return
            top = widget.winfo_rooty() - content.winfo_rooty()
            bottom = top + widget.winfo_height()
            visible_top = canvas.canvasy(0)
            visible_height = canvas.winfo_height()
            if top < visible_top:
                canvas.yview_moveto(max(0, top - 8) / max(1, content.winfo_height()))
            elif bottom > visible_top + visible_height:
                canvas.yview_moveto((bottom + 8 - visible_height) / max(1, content.winfo_height()))

        scroll_tag = "MarbleScapeSettingsScroll"
        root.bind_class(scroll_tag, "<MouseWheel>", scroll_settings)
        root.bind_class(scroll_tag, "<Button-4>", scroll_settings)
        root.bind_class(scroll_tag, "<Button-5>", scroll_settings)
        # Mouse clicks and dropdown focus changes must not move the viewport.
        # Only keyboard traversal brings an offscreen control into view.
        root.bind_class(scroll_tag, "<KeyPress-Tab>",
                        lambda _event: root.after_idle(reveal_focused_setting))
        root.bind_class(scroll_tag, "<KeyPress-ISO_Left_Tab>",
                        lambda _event: root.after_idle(reveal_focused_setting))

        def add_entry(parent, row, label, variable, width=16):
            ttk.Label(parent, text=label).grid(
                row=row, column=0, padx=(0, 10), pady=3, sticky="w"
            )
            entry = ttk.Entry(parent, textvariable=variable, width=width)
            entry.grid(row=row, column=1, pady=3, sticky="ew")
            return entry

        def add_combo(parent, row, label, variable, choices, width=18):
            ttk.Label(parent, text=label).grid(
                row=row, column=0, padx=(0, 10), pady=3, sticky="w"
            )
            combo = ttk.Combobox(
                parent,
                textvariable=variable,
                values=choices,
                state="readonly",
                width=width,
            )
            combo.grid(row=row, column=1, pady=3, sticky="ew")
            return combo

        def add_folder_picker(parent, row, label, key, default_folder, open_label):
            ttk.Label(parent, text=label).grid(
                row=row, column=0, padx=(0, 10), pady=3, sticky="w"
            )
            field = ttk.Frame(parent)
            field.grid(row=row, column=1, pady=3, sticky="ew")
            field.columnconfigure(0, weight=1)
            ttk.Entry(field, textvariable=variables[key], width=28).grid(
                row=0, column=0, padx=(0, 5), sticky="ew"
            )

            def choose_folder():
                configured = variables[key].get().strip()
                initial = (
                    resolve_script_relative_path(configured)
                    if configured else default_folder
                )
                while not initial.is_dir() and initial != initial.parent:
                    initial = initial.parent
                selected = filedialog.askdirectory(
                    parent=root,
                    title=label,
                    initialdir=str(initial),
                    mustexist=False,
                )
                if selected:
                    variables[key].set(selected)

            ttk.Button(field, text="Choose...", command=choose_folder).grid(
                row=0, column=1
            )

            def open_folder():
                try:
                    configured = variables[key].get().strip()
                    folder = (
                        resolve_script_relative_path(configured)
                        if configured else default_folder
                    )
                    folder.mkdir(parents=True, exist_ok=True)
                    os.startfile(str(folder.resolve()))
                except Exception as exc:
                    messagebox.showerror(
                        f"Unable to open {label.lower()}", str(exc), parent=root
                    )

            ttk.Button(field, text=open_label, command=open_folder).grid(
                row=1, column=1, pady=(4, 0), sticky="e"
            )
            ttk.Label(
                parent, text="Empty = default; relative paths use the script folder."
            ).grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(0, 3))

        def choose_background_color():
            try:
                initial_color = normalize_background_color(
                    variables["background_color"].get()
                )
            except ValueError as exc:
                messagebox.showerror("Invalid color", str(exc), parent=root)
                return

            selected = colorchooser.askcolor(
                color=initial_color,
                title="Choose background color",
                parent=root,
            )[1]
            if selected:
                variables["background_color"].set(
                    normalize_background_color(selected)
                )

        output_preset_update = {"active": False}

        def refresh_output_preset_labels(*_args):
            if output_preset_update["active"]:
                return
            output_preset_update["active"] = True
            try:
                try:
                    width = int(variables["width"].get())
                    configured_height = int(variables["height"].get())
                    ratio = parse_aspect_ratio(
                        variables["aspect_ratio"].get()
                    )
                    effective_height = (
                        round(width / ratio)
                        if configured_height == 0
                        else configured_height
                    )
                    resolution_label = resolution_label_by_size.get(
                        (width, effective_height),
                        "Custom",
                    )
                except (TypeError, ValueError, OverflowError):
                    resolution_label = "Custom"

                try:
                    current_ratio = parse_aspect_ratio(
                        variables["aspect_ratio"].get()
                    )
                    aspect_label = next(
                        (
                            label
                            for label, aspect_ratio
                            in aspect_label_to_value.items()
                            if math.isclose(
                                current_ratio,
                                parse_aspect_ratio(aspect_ratio),
                                rel_tol=1e-9,
                            )
                        ),
                        "Custom",
                    )
                except (TypeError, ValueError, OverflowError):
                    aspect_label = "Custom"

                variables["resolution_preset"].set(resolution_label)
                variables["aspect_ratio_preset"].set(aspect_label)
            finally:
                output_preset_update["active"] = False

        def select_resolution_preset(_event=None):
            selected = variables["resolution_preset"].get()
            if selected == "Custom":
                return
            width, height = resolution_label_to_size[selected]
            output_preset_update["active"] = True
            try:
                variables["width"].set(str(width))
                variables["height"].set("0")
                variables["aspect_ratio"].set(
                    aspect_ratio_for_dimensions(width, height)
                )
            finally:
                output_preset_update["active"] = False
            refresh_output_preset_labels()

        def select_aspect_ratio_preset(_event=None):
            selected = variables["aspect_ratio_preset"].get()
            if selected == "Custom":
                return
            output_preset_update["active"] = True
            try:
                variables["aspect_ratio"].set(
                    aspect_label_to_value[selected]
                )
                variables["height"].set("0")
            finally:
                output_preset_update["active"] = False
            refresh_output_preset_labels()

        render_quality_update = {"active": False}

        def refresh_render_quality_preset(*_args):
            if render_quality_update["active"]:
                return
            try:
                current_value = parse_render_scale_setting(
                    variables["render_scale"].get()
                )
            except ValueError:
                variables["render_quality_preset"].set("Custom")
                return
            current_label = next(
                (
                    label
                    for label, value in RENDER_QUALITY_MENU_CHOICES
                    if (
                        value == current_value
                        if isinstance(value, str)
                        else (
                            not isinstance(current_value, str)
                            and math.isclose(
                                float(value),
                                float(current_value),
                                rel_tol=1e-9,
                            )
                        )
                    )
                ),
                "Custom",
            )
            variables["render_quality_preset"].set(current_label)

        def select_render_quality_preset(_event=None):
            selected = variables["render_quality_preset"].get()
            if selected == "Custom":
                return
            render_quality_update["active"] = True
            try:
                value = render_quality_label_to_value[selected]
                variables["render_scale"].set(
                    value if isinstance(value, str) else number_text(value)
                )
            finally:
                render_quality_update["active"] = False
            refresh_render_quality_preset()

        def choose_backup_export():
            selected_path = filedialog.asksaveasfilename(
                parent=root,
                title="Export MarbleScape settings backup",
                initialdir=str(SCRIPT_DIR),
                initialfile=(
                    "marblescape-settings-"
                    f"{dt.datetime.now():%Y-%m-%d_%H%M%S}.json"
                ),
                defaultextension=".json",
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not selected_path:
                return
            try:
                saved_path = export_settings_backup(selected_path)
            except Exception as exc:
                messagebox.showerror(
                    "Unable to export backup",
                    str(exc),
                    parent=root,
                )
                return
            messagebox.showinfo(
                "Settings backup exported",
                f"Backup saved to:\n{saved_path}",
                parent=root,
            )

        def choose_backup_import():
            selected_path = filedialog.askopenfilename(
                parent=root,
                title="Import MarbleScape settings backup",
                initialdir=str(SCRIPT_DIR),
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not selected_path:
                return
            try:
                config_text, profile_library, startup_enabled = import_settings_backup(
                    selected_path
                )
            except Exception as exc:
                messagebox.showerror(
                    "Unable to import backup",
                    str(exc),
                    parent=root,
                )
                return

            confirmed = messagebox.askyesno(
                "Import settings backup",
                "Importing this backup replaces the current configuration "
                "and applies it immediately. Continue?",
                parent=root,
                icon="warning",
            )
            if not confirmed:
                return

            try:
                apply_settings_backup(
                    icon, config_text, profile_library, startup_enabled
                )
            except Exception as exc:
                messagebox.showerror(
                    "Unable to import backup",
                    str(exc),
                    parent=root,
                )
                return
            root.destroy()

        def select_projection(event=None):
            del event
            updates = dict(
                (key, value)
                for _section, key, value in projection_selection_updates(
                    variables["projection"].get()
                )
            )
            variables["view_preset"].set(next(
                label for label, value in preset_label_to_value.items()
                if value == "full_earth"
            ))
            variables["zoom"].set(str(updates["zoom"]))
            variables["fit_mode"].set(updates["fit_mode"])

        def refresh_projection_choices():
            choices = available_projection_choices()
            projection_combo.configure(values=choices)
            if variables["projection"].get() not in choices:
                variables["projection"].set("GEOS: MSG FES, MTG FD")
                select_projection()

        def select_view_preset(event=None):
            del event
            preset_name = preset_label_to_value[variables["view_preset"].get()]
            if preset_name not in VIEW_PRESET_PROFILES:
                return
            profile = VIEW_PRESET_PROFILES[preset_name]
            variables["projection"].set(profile["projection"])
            variables["fit_mode"].set(profile["fit_mode"])
            variables["zoom"].set(number_text(profile["zoom"]))
            layer_label = next(
                label for label, layer_name in layer_label_to_value.items()
                if layer_name == profile["satellite_layer"]
            )
            variables["satellite_layer"].set(layer_label)
            try:
                source_settings.select_eumetsat_layer(profile["satellite_layer"])
            except NameError:
                pass

        try:
            output_monitors = list_windows_wallpaper_monitors()
        except Exception as exc:
            log(f"Unable to list output devices: {exc}")
            output_monitors = []
        output_device_ids = {"All monitors": None}
        for index, monitor in enumerate(output_monitors, start=1):
            left, top, right, bottom = monitor["rect"]
            label = f"Display {index} ({right - left} x {bottom - top})"
            output_device_ids[label] = monitor["id"]
        monitor_positions_draft = dict(WINDOWS_WALLPAPER_MONITOR_POSITIONS)
        monitor_outputs_draft = deepcopy(WINDOWS_WALLPAPER_MONITOR_OUTPUTS)
        global_position_draft = {"value": WINDOWS_WALLPAPER_POSITION}
        global_output_draft = {
            key: variables[key].get() for key in MONITOR_OUTPUT_FIELDS
        }
        switching_output_device = {"active": False}
        active_output_device = {"id": None}
        position_display = tk.StringVar(
            value=WALLPAPER_POSITION_LABELS[variables["position"].get()]
        )

        def update_position_display(*_args):
            position_display.set(WALLPAPER_POSITION_LABELS[variables["position"].get()])

        variables["position"].trace_add("write", update_position_display)

        def select_output_device(_event=None):
            monitor_id = output_device_ids[variables["output_device"].get()]
            restore_wallpaper_button.configure(
                state="normal" if monitor_id is not None else "disabled"
            )
            switching_output_device["active"] = True
            try:
                active_output_device["id"] = monitor_id
                variables["position"].set(
                    monitor_positions_draft.get(monitor_id, global_position_draft["value"])
                    if monitor_id else global_position_draft["value"]
                )
                overrides = monitor_outputs_draft.get(monitor_id, {}) if monitor_id else {}
                for key in MONITOR_OUTPUT_FIELDS:
                    variables[key].set(str(overrides.get(key, global_output_draft[key])))
            finally:
                switching_output_device["active"] = False

        def select_wallpaper_position(*_args):
            if switching_output_device["active"]:
                return
            monitor_id = active_output_device["id"]
            value = variables["position"].get()
            if monitor_id:
                if value == global_position_draft["value"]:
                    monitor_positions_draft.pop(monitor_id, None)
                else:
                    monitor_positions_draft[monitor_id] = value
            else:
                global_position_draft["value"] = value

        def select_monitor_output_setting(key):
            if switching_output_device["active"]:
                return
            monitor_id = active_output_device["id"]
            if monitor_id is None:
                global_output_draft[key] = variables[key].get()
                return
            overrides = monitor_outputs_draft.setdefault(monitor_id, {})
            keys = ("width", "height", "aspect_ratio") if key in {
                "width", "height", "aspect_ratio"
            } else (key,)
            if len(keys) == 3:
                if all(variables[field].get() == str(global_output_draft[field])
                       for field in keys):
                    for field in keys:
                        overrides.pop(field, None)
                else:
                    for field in keys:
                        overrides[field] = variables[field].get()
            else:
                value = variables[key].get()
                if value == str(global_output_draft[key]):
                    overrides.pop(key, None)
                else:
                    overrides[key] = value
            if not overrides:
                monitor_outputs_draft.pop(monitor_id, None)

        variables["position"].trace_add("write", select_wallpaper_position)
        for key in MONITOR_OUTPUT_FIELDS:
            variables[key].trace_add(
                "write", lambda *_args, field=key: select_monitor_output_setting(field)
            )

        wallpaper_frame = ttk.LabelFrame(general_tab, text="Wallpaper", padding=8)
        wallpaper_frame.grid(row=1, column=0, pady=(0, 8), sticky="ew")
        ttk.Checkbutton(
            wallpaper_frame,
            text="Set wallpaper automatically",
            variable=variables["set_wallpaper"],
        ).grid(row=0, column=0, columnspan=2, pady=3, sticky="w")
        ttk.Checkbutton(
            wallpaper_frame,
            text="Start with Windows",
            variable=variables["start_with_windows"],
        ).grid(row=1, column=0, columnspan=2, pady=3, sticky="w")

        ttk.Label(wallpaper_frame, wraplength=640, text=(
            "Choose each display under Output device and set its position under Output. "
            "Do not update keeps its current wallpaper. Restore previous wallpaper "
            "uses a saved copy when one is available."
        )).grid(row=2, column=0, columnspan=2, pady=(4, 0), sticky="w")

        display_time_frame = ttk.LabelFrame(general_tab, text="Date and time", padding=8)
        display_time_frame.grid(row=2, column=0, pady=(0, 8), sticky="ew")
        add_combo(
            display_time_frame, 0, "Time zone", variables["time_zone"],
            tuple(time_zone_label_to_value), width=28,
        )
        ttk.Label(display_time_frame, wraplength=640, text=(
            "System time uses the Windows time zone and daylight-saving rules. "
            "Provider and cache timestamps remain stored in UTC."
        )).grid(row=1, column=0, columnspan=2, pady=(4, 0), sticky="w")

        output_device_frame = ttk.LabelFrame(general_tab, text="Output device", padding=8)
        output_device_frame.grid(row=4, column=0, pady=(0, 8), sticky="ew")
        output_device_combo = add_combo(
            output_device_frame, 0, "Screen / monitor", variables["output_device"],
            tuple(output_device_ids), width=38,
        )
        output_device_combo.bind("<<ComboboxSelected>>", select_output_device)

        def refresh_output_device_choices():
            try:
                attached = list_windows_wallpaper_monitors()
            except Exception as exc:
                log(f"Unable to refresh output devices: {exc}")
                return
            choices = {"All monitors": None}
            for index, monitor in enumerate(attached, start=1):
                left, top, right, bottom = monitor["rect"]
                choices[f"Display {index} ({right - left} x {bottom - top})"] = monitor["id"]
            if choices == output_device_ids:
                return
            selected_id = active_output_device["id"]
            output_device_ids.clear()
            output_device_ids.update(choices)
            output_device_combo.configure(values=tuple(choices))
            selected_label = next(
                (label for label, monitor_id in choices.items() if monitor_id == selected_id),
                "All monitors",
            )
            if selected_label != variables["output_device"].get():
                variables["output_device"].set(selected_label)
                select_output_device()

        output_device_combo.configure(postcommand=refresh_output_device_choices)
        def restore_selected_wallpaper():
            monitor_id = active_output_device["id"]
            if monitor_id is None:
                return
            try:
                restore_previous_wallpaper(monitor_id)
            except FileNotFoundError as exc:
                messagebox.showinfo("Restore previous wallpaper", str(exc), parent=root)
                return
            except Exception as exc:
                messagebox.showerror("Unable to restore wallpaper", str(exc), parent=root)
                return
            monitor_positions_draft[monitor_id] = "none"
            variables["position"].set("none")
            request_runtime_configuration_reload(icon)

        restore_wallpaper_button = ttk.Button(
            output_device_frame, text="Restore previous wallpaper",
            command=restore_selected_wallpaper,
        )
        restore_wallpaper_button.grid(row=2, column=1, pady=(5, 0), sticky="e")
        restore_wallpaper_button.configure(state="disabled")
        ttk.Label(output_device_frame, text=(
            "All monitors sets the defaults. Select a display for its own Output "
            "settings. A disconnected display keeps its settings for the next wallpaper update after reconnection."
        ), wraplength=640).grid(row=1, column=0, columnspan=2, sticky="w")

        output_frame = ttk.LabelFrame(general_tab, text="Output", padding=8)
        output_frame.grid(row=5, column=0, pady=(0, 8), sticky="ew")
        resolution_combo = add_combo(
            output_frame,
            0,
            "Resolution preset",
            variables["resolution_preset"],
            (*resolution_label_to_size, "Custom"),
            width=38,
        )
        resolution_combo.bind(
            "<<ComboboxSelected>>",
            select_resolution_preset,
        )
        aspect_combo = add_combo(
            output_frame,
            1,
            "Aspect ratio preset",
            variables["aspect_ratio_preset"],
            (*aspect_label_to_value, "Custom"),
            width=38,
        )
        aspect_combo.bind(
            "<<ComboboxSelected>>",
            select_aspect_ratio_preset,
        )
        add_entry(output_frame, 2, "Width", variables["width"])
        add_entry(output_frame, 3, "Height (0 = auto)", variables["height"])
        add_entry(output_frame, 4, "Aspect ratio", variables["aspect_ratio"])
        render_quality_combo = add_combo(
            output_frame,
            5,
            "Render quality preset",
            variables["render_quality_preset"],
            (*render_quality_label_to_value, "Custom"),
            width=30,
        )
        render_quality_combo.bind(
            "<<ComboboxSelected>>",
            select_render_quality_preset,
        )
        add_entry(
            output_frame,
            6,
            "Render quality factor",
            variables["render_scale"],
        )
        ttk.Label(output_frame, text="Background color").grid(
            row=7, column=0, padx=(0, 10), pady=3, sticky="w"
        )
        color_frame = ttk.Frame(output_frame)
        color_frame.grid(row=7, column=1, pady=3, sticky="ew")
        ttk.Entry(
            color_frame,
            textvariable=variables["background_color"],
            width=11,
        ).grid(row=0, column=0, padx=(0, 5), sticky="ew")
        ttk.Button(
            color_frame,
            text="Choose...",
            command=choose_background_color,
        ).grid(row=0, column=1)
        for key in ("width", "height", "aspect_ratio"):
            variables[key].trace_add("write", refresh_output_preset_labels)
        variables["render_scale"].trace_add(
            "write",
            refresh_render_quality_preset,
        )
        noaa_quality_hint = ttk.Label(output_frame, wraplength=640, text=(
            "GOES / Solar / Himawari / CIRA SLIDER render quality: controlled by Source resolution. "
            "Largest available uses the most detailed source image; resizing uses "
            "high-quality Lanczos filtering. A WMS render factor adds no source detail."
        ))
        position_combo = add_combo(
            output_frame, 8, "Position", position_display,
            tuple(WALLPAPER_POSITION_LABELS.values()), width=38,
        )
        position_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: variables["position"].set(next(
                name for name, label in WALLPAPER_POSITION_LABELS.items()
                if label == position_display.get()
            )),
        )
        noaa_quality_hint.grid(row=9, column=0, columnspan=2, pady=3, sticky="w")

        copernicus_hidden_output_controls = [
            widget for widget in output_frame.winfo_children()
            if int(widget.grid_info().get("row", -1)) == 7
        ]

        wms_output_controls = [widget for widget in output_frame.winfo_children()
                               if int(widget.grid_info().get("row", -1)) in {5, 6}]

        def source_changed(provider):
            for widget in wms_output_controls:
                if provider == "eumetsat":
                    widget.grid()
                else:
                    widget.grid_remove()
            if source_settings.view_defaults_requested:
                variables["zoom"].set("1.1" if provider == "eumetsat" else "1")
            if provider == "copernicus":
                for widget in copernicus_hidden_output_controls:
                    widget.grid_remove()
            else:
                for widget in copernicus_hidden_output_controls:
                    widget.grid()
            if provider == "eumetsat":
                noaa_quality_hint.grid_remove()
            elif provider in {"goes_east", "goes_west", "solar", "himawari", "slider"}:
                noaa_quality_hint.configure(text=(
                    "GOES / Solar / Himawari / CIRA SLIDER render quality: controlled by Source resolution. "
                    "Largest available uses the most detailed source image; resizing uses "
                    "high-quality Lanczos filtering. A WMS render factor adds no source detail."
                ))
                noaa_quality_hint.grid()
            elif provider == "worldview":
                noaa_quality_hint.configure(text=(
                    "NASA Worldview detail is controlled by Render resolution in Source. "
                    "Largest available uses the largest safe GIBS WMS render for the selected layer; "
                    "resizing uses high-quality Lanczos filtering."
                ))
                noaa_quality_hint.grid()
            else:
                noaa_quality_hint.grid_remove()

        def copernicus_output_size():
            try:
                width = int(variables["width"].get())
                configured_height = int(variables["height"].get())
                ratio = parse_aspect_ratio(variables["aspect_ratio"].get())
                height = configured_height or round(width / ratio)
                if width <= 0 or height <= 0:
                    raise ValueError
                return width, height
            except (TypeError, ValueError, ZeroDivisionError, OverflowError):
                return get_output_dimensions()

        source_settings = SourceSettings(
            image_tab, IMAGE_SOURCE, SOURCE_PROFILES,
            timeout=NETWORK_TIMEOUT_SECONDS, user_agent=USER_AGENT,
            on_change=source_changed, client=get_catalogue_client(),
            copernicus_auth={"client_id": COPERNICUS_CLIENT_ID,
                             "client_secret": COPERNICUS_CLIENT_SECRET},
            output_size=copernicus_output_size,
            eumetsat_layer=selected_wms_layer,
        )
        source_settings.frame.grid(row=0, column=0, pady=(0, 8), sticky="ew")
        generic_view_frame = source_settings.generic_view_frame
        add_combo(
            generic_view_frame, 0, "Fit mode", variables["fit_mode"], ("fit", "crop"),
            width=SOURCE_COMBO_WIDTH,
        )
        add_entry(generic_view_frame, 1, "Zoom", variables["zoom"])
        ttk.Label(generic_view_frame, wraplength=640, text=(
            "Fit keeps the whole view; Crop fills the output and trims the edges. "
            "General > Output > Position then places the finished file on the desktop."
        )).grid(row=2, column=0, columnspan=2, pady=(3, 0), sticky="w")
        preset_frame = source_settings.eumetsat_view_frame
        configure_source_columns(preset_frame)
        projection_combo = add_combo(
            preset_frame, 0, "Projection", variables["projection"],
            available_projection_choices(), width=SOURCE_COMBO_WIDTH,
        )
        projection_combo.bind("<<ComboboxSelected>>", select_projection)
        add_combo(
            preset_frame, 1, "Fit mode", variables["fit_mode"], ("fit", "crop"),
            width=SOURCE_COMBO_WIDTH,
        )
        add_entry(preset_frame, 2, "Zoom", variables["zoom"])
        preset_combo = add_combo(preset_frame, 3, "Preset", variables["view_preset"],
                                 tuple(preset_label_to_value), width=SOURCE_COMBO_WIDTH)
        preset_combo.bind("<<ComboboxSelected>>", select_view_preset)
        ttk.Checkbutton(
            preset_frame,
            text="Black TrueColor night side",
            variable=variables["truecolor_black_night"],
        ).grid(row=4, column=0, columnspan=2, pady=3, sticky="w")
        ttk.Label(preset_frame, wraplength=640, text=(
            "Selecting a preset sets its satellite layer, projection, fit mode, and zoom."
        )).grid(row=5, column=0, columnspan=2, pady=(3, 0), sticky="w")
        ttk.Label(
            preset_frame,
            text="All projections published by the EUMETSAT viewer are available.",
        ).grid(row=6, column=0, columnspan=2, pady=3, sticky="w")
        ttk.Label(
            preset_frame,
            text="Coverage depends on the selected satellite layer.",
        ).grid(row=7, column=0, columnspan=2, pady=3, sticky="w")
        ttk.Label(
            preset_frame,
            text="Changing projection resets the view to Full Earth.\n"
                 "Regional presets use Geographic.",
        ).grid(row=8, column=0, columnspan=2, pady=3, sticky="w")
        ttk.Label(preset_frame, wraplength=640, text=(
            "Fit keeps the whole view; Crop fills the output and trims the edges. "
            "General > Wallpaper > Position then places the finished file on the desktop."
        )).grid(row=9, column=0, columnspan=2, pady=3, sticky="w")
        source_changed(IMAGE_SOURCE)
        ttk.Label(image_tab, textvariable=status_variables["image_source"],
                  wraplength=640).grid(row=1, column=0, pady=(0, 8), sticky="ew")

        update_frame = ttk.LabelFrame(general_tab, text="Updates", padding=8)
        update_frame.grid(row=3, column=0, pady=(0, 8), sticky="ew")
        add_entry(
            update_frame,
            0,
            "Interval (minutes)",
            variables["update_interval_minutes"],
        )

        download_display_frame = ttk.LabelFrame(
            download_tab, text="Download display", padding=8
        )
        download_display_frame.grid(row=0, column=0, pady=(0, 8), sticky="ew")
        ttk.Checkbutton(
            download_display_frame,
            text="Show download speed",
            variable=variables["show_download_speed"],
        ).grid(row=0, column=0, columnspan=2, pady=3, sticky="w")
        add_combo(
            download_display_frame,
            1,
            "Speed unit",
            variables["download_speed_unit"],
            DOWNLOAD_SPEED_UNIT_CHOICES,
            width=18,
        )
        ttk.Checkbutton(
            download_display_frame,
            text="Show percentage and downloaded size",
            variable=variables["show_download_progress"],
        ).grid(row=2, column=0, columnspan=2, pady=3, sticky="w")
        ttk.Checkbutton(
            download_display_frame,
            text="Show progress bar",
            variable=variables["show_download_progress_bar"],
        ).grid(row=3, column=0, columnspan=2, pady=3, sticky="w")
        ttk.Checkbutton(
            download_display_frame,
            text="Keep completed download visible until next download",
            variable=variables["keep_completed_download_visible"],
        ).grid(row=4, column=0, columnspan=2, pady=3, sticky="w")
        ttk.Label(download_display_frame, wraplength=640, text=(
            "MarbleScape shows an exact total and percentage when the server "
            "provides Content-Length for the complete transfer. Tiled and "
            "multi-request images remain indeterminate until their total is known."
        )).grid(row=5, column=0, columnspan=2, pady=(5, 0), sticky="w")

        download_retry_frame = ttk.LabelFrame(
            download_tab, text="Download retries", padding=8
        )
        download_retry_frame.grid(row=1, column=0, pady=(0, 8), sticky="ew")
        add_combo(
            download_retry_frame, 0, "Retries after first attempt",
            variables["download_retries"], tuple(str(value) for value in range(1, 10)),
            width=8,
        )
        ttk.Label(download_retry_frame, wraplength=640, text=(
            "Applies globally to temporary image-transfer errors. "
            "1-9 retries mean 2-10 total attempts. Invalid requests and "
            "authentication errors fail immediately."
        )).grid(row=1, column=0, columnspan=2, pady=(5, 0), sticky="w")

        catalogue_retry_frame = ttk.LabelFrame(
            download_tab, text="Catalogue retries", padding=8
        )
        catalogue_retry_frame.grid(row=2, column=0, pady=(0, 8), sticky="ew")
        add_combo(
            catalogue_retry_frame, 0, "Retries after first attempt",
            variables["catalogue_retries"], tuple(str(value) for value in range(1, 10)),
            width=8,
        )
        ttk.Label(catalogue_retry_frame, wraplength=640, text=(
            "Applies globally when catalogue metadata cannot be refreshed. "
            "1-9 retries mean 2-10 total attempts. If all attempts fail, "
            "MarbleScape uses the most recent cached catalogue when available."
        )).grid(row=1, column=0, columnspan=2, pady=(5, 0), sticky="w")

        def request_picture_from_settings():
            if not force_loading_is_enabled(None):
                return
            force_picture_button.state(["disabled"])
            force_loading_new_picture(icon, None)

        image_update_frame = ttk.LabelFrame(
            image_tab, text="Image updates", padding=8
        )
        image_update_frame.grid(row=2, column=0, pady=(0, 8), sticky="ew")
        ttk.Checkbutton(
            image_update_frame,
            text="Check for and download newer images",
            variable=variables["check_for_source_updates"],
        ).grid(row=0, column=0, columnspan=2, pady=(0, 4), sticky="w")
        ttk.Label(
            image_update_frame,
            text=("When disabled, MarbleScape downloads the newest image once if no "
                  "matching local image exists. Force loading new picture still performs "
                  "a manual refresh. This setting is saved in image profiles."),
            wraplength=620, justify="left",
        ).grid(row=1, column=0, columnspan=2, pady=(0, 6), sticky="w")
        force_picture_button = ttk.Button(
            image_update_frame,
            text="Force loading new picture",
            command=request_picture_from_settings,
        )
        force_picture_button.grid(
            row=2, column=0, sticky="w"
        )

        def capture_image_form(validated_updates=None):
            provider, selections = source_settings.get_selection()
            values = {key: variable.get() for key, variable in variables.items()}
            values.update(global_output_draft)
            values["monitor_positions"] = dict(monitor_positions_draft)
            values["monitor_output_settings"] = deepcopy(monitor_outputs_draft)
            values["view_preset"] = preset_label_to_value[values["view_preset"]]
            # Saving an Image snapshot does not depend on unfinished edits in
            # General or History. Apply validates those tabs separately.
            values.update(position=WINDOWS_WALLPAPER_POSITION, time_zone=DISPLAY_TIME_ZONE,
                          update_interval_minutes=str(UPDATE_INTERVAL_MINUTES),
                          max_files=str(HISTORY_MAX_FILES), years=str(HISTORY_RETENTION_YEARS),
                          months=str(HISTORY_RETENTION_MONTHS), days=str(HISTORY_RETENTION_DAYS),
                          hours=str(HISTORY_RETENTION_HOURS), minutes=str(HISTORY_RETENTION_MINUTES),
                          retention_mode=HISTORY_RETENTION_MODE, history_folder=CUSTOM_HISTORY_FOLDER)
            updates = (normalize_settings_form_values(values, provider=provider)
                       if validated_updates is None else validated_updates)
            snapshot = deepcopy(image_form_state["base"])
            for section, key, value in updates:
                if section in {"view", "output"}:
                    snapshot[section][key] = value
            snapshot.update(
                source={
                    "provider": provider,
                    "check_for_updates": bool(
                        variables["check_for_source_updates"].get()
                    ),
                },
                sources=selections,
            )
            if provider == "eumetsat":
                selected_layer = source_settings.get_eumetsat_layer()
                for layer in snapshot["layers"]:
                    if layer.get("kind") == "wms" and layer.get("enabled", True):
                        layer["name"] = selected_layer
                        break
            return snapshot

        def load_image_form(snapshot):
            # Validate every field before changing any Tk variable or form draft.
            # This pure check must never swap the worker's runtime configuration.
            snapshot = normalize_image_settings_snapshot(snapshot)
            # Profiles are form drafts until Apply, just like all other settings.
            provider, selections = normalize_source_configuration(
                snapshot["source"]["provider"], snapshot["sources"])
            for section, fields in IMAGE_SETTING_FIELDS.items():
                if any(key not in snapshot[section] for key in fields):
                    raise ValueError("This profile has incomplete Image settings.")
            view, output = snapshot["view"], snapshot["output"]
            selected_layer = next((entry.get("name") for entry in snapshot["layers"]
                                   if entry.get("kind") == "wms" and entry.get("enabled", True)), None)
            preset_label = next((label for label, value in preset_label_to_value.items()
                                 if value == view["preset"]), None)
            if preset_label is None:
                if provider == "eumetsat" and view["preset"] not in VIEW_PRESETS:
                    raise ValueError("The profile contains an unknown EUMETSAT preset.")
                preset_label = f"Custom preset ({view['preset']})"
                preset_label_to_value[preset_label] = view["preset"]
                preset_combo.configure(values=tuple(preset_label_to_value))
            layer_label = next((label for label, value in layer_label_to_value.items()
                                if value == selected_layer), None)
            if layer_label is None:
                layer_label = f"Custom layer ({selected_layer})"
                layer_label_to_value[layer_label] = selected_layer
            image_form_state.update(base=deepcopy(snapshot), loaded=True)
            for key in ("projection", "fit_mode", "zoom", "truecolor_black_night"):
                variables[key].set(view[key])
            variables["view_preset"].set(preset_label)
            variables["satellite_layer"].set(layer_label)
            variables["check_for_source_updates"].set(
                snapshot["source"].get("check_for_updates", True)
            )
            switching_output_device["active"] = True
            try:
                for key in (*MONITOR_OUTPUT_FIELDS, "latest_folder"):
                    value = output[key]
                    if key == "aspect_ratio" and not value:
                        value = aspect_ratio_for_dimensions(output["width"], output["height"])
                    variables[key].set(value)
                    if key in global_output_draft:
                        global_output_draft[key] = str(value)
            finally:
                switching_output_device["active"] = False
            select_output_device()
            if provider == "eumetsat":
                refresh_projection_choices()
            else:
                # Retain unused WMS values in a non-EUMETSAT profile without
                # changing the source-specific selections.
                projection_combo.configure(values=available_projection_choices())
            if provider == "eumetsat":
                selections["eumetsat"]["layer"] = selected_layer
            source_settings.set_selection(provider, selections)
            if provider == "eumetsat":
                source_settings.select_eumetsat_layer(selected_layer)
            notebook.select(image_tab.master.master)

        artifact_frame = ttk.LabelFrame(
            image_tab, text="Satellite imagery notice", padding=8
        )
        artifact_frame.grid(row=5, column=0, pady=(0, 8), sticky="ew")
        ttk.Label(
            artifact_frame,
            text=("Satellite observations can contain seams, missing or partial scans, "
                  "day/night transitions, compression artifacts and provider annotations. "
                  "These source artifacts may remain visible in the wallpaper."),
            wraplength=640, justify="left",
        ).grid(row=0, column=0, sticky="w")

        def profile_status_text():
            with ROTATION_STATUS_LOCK:
                state = dict(ROTATION_STATUS)
            message = state["text"]
            if state["deadline"] is not None:
                remaining = max(0, state["deadline"] - time.monotonic())
                try:
                    due = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=remaining)
                    selected_zone = time_zone_label_to_value.get(
                        variables["time_zone"].get(), DISPLAY_TIME_ZONE
                    )
                    message += " Next profile: " + format_display_datetime(
                        due, selected_zone, include_seconds=True
                    ) + "."
                except OverflowError:
                    pass
            profile_ids = [item["id"] for item in IMAGE_PROFILE_LIBRARY.get("items", [])]
            try:
                cache_entries = get_profile_cache().entries(profile_ids)
            except Exception:
                cache_entries = {}
            return {
                "text": message,
                "active_profile_id": state.get("active_profile_id"),
                "profiles": cache_entries,
                "wallpaper_position": variables["position"].get(),
                "display_time_zone": time_zone_label_to_value.get(
                    variables["time_zone"].get(), DISPLAY_TIME_ZONE
                ),
            }

        def apply_image_profile(snapshot):
            normalized = normalize_image_settings_snapshot(snapshot)
            requested_library = profile_settings.get_library()
            requested_profile_columns = profile_settings.get_visible_columns()
            requested_auth = source_settings.get_copernicus_auth(require=False)

            def transform(text):
                updated = replace_image_settings(text, normalized)
                updated = replace_copernicus_auth_configuration(
                    updated, requested_auth
                )
                updated = ensure_profile_list_configuration_section(updated)
                return replace_toml_section_value(
                    updated, "profile_list", "visible_columns",
                    list(requested_profile_columns),
                )

            changed = update_active_configuration_and_profiles(
                transform, requested_library
            )
            load_image_form(normalized)
            image_form_state.update(base=deepcopy(normalized), loaded=False)
            if changed:
                request_runtime_configuration_reload(icon)

        from marblescape_profile_settings import ProfilesSettings
        profile_settings = ProfilesSettings(profiles_tab, IMAGE_PROFILE_LIBRARY,
                                             capture_image_form, load_image_form,
                                             on_apply=apply_image_profile,
                                             status=profile_status_text,
                                             visible_columns=PROFILE_LIST_VISIBLE_COLUMNS)
        profile_settings.frame.grid(row=0, column=0, sticky="ew")

        latest_folder_frame = ttk.LabelFrame(
            history_tab, text="Latest image folder", padding=8
        )
        latest_folder_frame.grid(row=0, column=0, pady=(0, 8), sticky="ew")
        add_folder_picker(
            latest_folder_frame, 0, "Custom latest folder", "latest_folder",
            CONTENT_DIR / LATEST_DIRECTORY_NAME, "Open latest folder",
        )

        history_frame = ttk.LabelFrame(history_tab, text="History", padding=8)
        history_frame.grid(row=1, column=0, pady=(0, 8), sticky="ew")
        ttk.Checkbutton(
            history_frame,
            text="Enable history",
            variable=variables["history_enabled"],
        ).grid(row=0, column=0, columnspan=2, pady=3, sticky="w")
        add_combo(
            history_frame,
            1,
            "Retention mode",
            variables["retention_mode"],
            ("count", "time", "both"),
        )
        add_entry(history_frame, 2, "Maximum files", variables["max_files"])

        age_frame = ttk.Frame(history_frame)
        age_frame.grid(row=3, column=0, columnspan=2, pady=(6, 0), sticky="ew")
        for column, key in enumerate(("years", "months", "days", "hours", "minutes")):
            ttk.Label(age_frame, text=key.capitalize()).grid(
                row=0, column=column, padx=3, sticky="w"
            )
            ttk.Entry(age_frame, textvariable=variables[key], width=8).grid(
                row=1, column=column, padx=3, sticky="ew"
            )

        add_folder_picker(
            history_frame, 4, "Custom history folder", "history_folder",
            CONTENT_DIR / HISTORY_DIRECTORY_NAME, "Open history folder",
        )

        status_frame = ttk.LabelFrame(
            history_tab,
            text="Status and storage",
            padding=8,
        )
        status_frame.grid(
            row=2,
            column=0,
            columnspan=2,
            pady=(0, 8),
            sticky="nsew",
        )
        status_rows = (
            ("Normal latest images", "latest_images"),
            ("Current image size", "current_image_size"),
            ("Estimated history images", "estimated_history_images"),
            ("Estimated maximum total", "estimated_maximum_total"),
            ("Total storage estimate", "total_storage_estimate"),
            (
                "Currently used disk space (latest + cache + history)",
                "currently_used_disk_space",
            ),
            ("Next check", "next_check"),
        )
        for row, (label, key) in enumerate(status_rows):
            ttk.Label(status_frame, text=label).grid(
                row=row,
                column=0,
                padx=(0, 12),
                pady=2,
                sticky="w",
            )
            ttk.Label(
                status_frame,
                textvariable=status_variables[key],
            ).grid(row=row, column=1, pady=2, sticky="w")

        def clear_history_from_settings():
            clear_history_button.state(["disabled"])
            try:
                cleared = clear_history_images()
                status_variables["history_action"].set(
                    f"Cleared {cleared['files']} history image(s) "
                    f"({format_disk_usage(cleared['bytes'])})."
                )
            except Exception as exc:
                status_variables["history_action"].set("Unable to clear History.")
                messagebox.showerror(
                    "Unable to clear history", str(exc), parent=root
                )
            finally:
                clear_history_button.state(["!disabled"])

        history_action_row = len(status_rows)
        clear_history_button = ttk.Button(
            status_frame,
            text="Clear history",
            command=clear_history_from_settings,
        )
        clear_history_button.grid(
            row=history_action_row, column=0, pady=(7, 0), sticky="w"
        )
        ttk.Label(
            status_frame,
            textvariable=status_variables["history_action"],
            wraplength=470,
            justify="left",
        ).grid(
            row=history_action_row, column=1, pady=(7, 0), sticky="w"
        )

        cache_frame = ttk.LabelFrame(
            history_tab,
            text="Profile image cache",
            padding=8,
        )
        cache_frame.grid(
            row=3,
            column=0,
            columnspan=2,
            pady=(0, 8),
            sticky="ew",
        )
        ttk.Label(
            cache_frame,
            text=(
                "Cached profile images avoid downloading and rendering an unchanged "
                "image again. Clearing does not remove Latest or History images."
            ),
            wraplength=590,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, pady=(0, 6), sticky="w")
        ttk.Label(cache_frame, text="Stored cache").grid(
            row=1, column=0, padx=(0, 12), sticky="w"
        )
        ttk.Label(cache_frame, textvariable=status_variables["profile_cache"]).grid(
            row=1, column=1, sticky="w"
        )

        def clear_cache_from_settings():
            clear_cache_button.state(["disabled"])
            try:
                cleared = clear_profile_image_cache()
                status_variables["cache_action"].set(
                    f"Cleared {cleared['files']} cached image(s) "
                    f"({format_disk_usage(cleared['bytes'])})."
                )
            except Exception as exc:
                status_variables["cache_action"].set("Unable to clear the profile cache.")
                messagebox.showerror(
                    "Unable to clear cache", str(exc), parent=root
                )
            finally:
                clear_cache_button.state(["!disabled"])

        clear_cache_button = ttk.Button(
            cache_frame,
            text="Clear cache",
            command=clear_cache_from_settings,
        )
        clear_cache_button.grid(row=2, column=0, pady=(7, 0), sticky="w")
        ttk.Label(
            cache_frame,
            textvariable=status_variables["cache_action"],
            wraplength=470,
            justify="left",
        ).grid(row=2, column=1, padx=(12, 0), pady=(7, 0), sticky="w")

        def refresh_status_section():
            selected_time_zone = time_zone_label_to_value.get(
                variables["time_zone"].get(), DISPLAY_TIME_ZONE
            )
            status_variables["image_source"].set(
                image_source_status_text(selected_time_zone) if IMAGE_SOURCE != "eumetsat"
                else "Active source: EUMETSAT"
            )
            force_picture_button.state(
                ["!disabled"] if force_loading_is_enabled(None) else ["disabled"]
            )
            storage = get_storage_status()
            current_size = storage["current_image_size"]
            estimated_bytes = storage["estimated_total_bytes"]

            status_variables["activity"].set(
                update_activity_status_text(None)
            )
            status_variables["latest_images"].set(
                str(storage["latest_images"])
            )
            status_variables["current_image_size"].set(
                format_bytes(current_size)
                if current_size is not None
                else "No image available"
            )
            status_variables["estimated_history_images"].set(
                str(storage["estimated_history_images"])
            )
            status_variables["estimated_maximum_total"].set(
                f'{storage["estimated_maximum_images"]} images'
            )
            status_variables["total_storage_estimate"].set(
                format_bytes(estimated_bytes)
                if estimated_bytes is not None
                else "Unavailable until the first image"
            )
            status_variables["currently_used_disk_space"].set(
                format_disk_usage(storage["used_bytes"])
            )
            status_variables["profile_cache"].set(
                f"{storage['cache_files']} image(s) for "
                f"{storage['cache_profiles']} profile(s), "
                f"{format_disk_usage(storage['cache_bytes'])}"
            )
            status_variables["next_check"].set(
                format_next_check_status(selected_time_zone)
            )
            root.after(1000, refresh_status_section)

        refresh_status_section()

        backup_frame = ttk.LabelFrame(backup_tab, text="Backup", padding=8)
        backup_frame.grid(
            row=0,
            column=0,
            columnspan=2,
            pady=(0, 8),
            sticky="nsew",
        )
        ttk.Label(
            backup_frame,
            text="Export or restore the complete saved configuration.",
        ).grid(row=0, column=0, padx=(0, 12), sticky="w")
        ttk.Button(
            backup_frame,
            text="Export...",
            command=choose_backup_export,
        ).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(
            backup_frame,
            text="Import...",
            command=choose_backup_import,
        ).grid(row=0, column=2)

        sources_frame = ttk.LabelFrame(
            sources_tab, text="Satellite imagery viewers", padding=10
        )
        sources_frame.grid(row=0, column=0, sticky="ew")
        sources_frame.columnconfigure(0, weight=1)
        ttk.Label(
            sources_frame,
            text=("Open a viewer to browse the imagery used by MarbleScape. "
                  "Select a URL to open it in your default browser."),
            wraplength=650, justify="left",
        ).grid(row=0, column=0, pady=(0, 8), sticky="w")
        allowed_source_urls = {
            url for _provider, urls in SOURCE_VIEWER_URLS for url in urls
        }

        def open_source_reference(url):
            if url not in allowed_source_urls:
                return
            try:
                if not webbrowser.open(url, new=2):
                    raise RuntimeError("The web browser could not be opened.")
            except Exception as exc:
                messagebox.showerror(
                    "Unable to open source", str(exc), parent=root
                )

        source_row = 1
        for provider_name, urls in SOURCE_VIEWER_URLS:
            ttk.Label(
                sources_frame, text=provider_name,
                font=("TkDefaultFont", 10, "bold"),
            ).grid(row=source_row, column=0, pady=(8 if source_row > 1 else 0, 2), sticky="w")
            source_row += 1
            for url in urls:
                link = tk.Label(
                    sources_frame, text=url, fg="#0000EE", cursor="hand2",
                    anchor="w", justify="left", wraplength=650,
                )
                link.grid(row=source_row, column=0, pady=1, sticky="ew")
                link.bind(
                    "<Button-1>",
                    lambda _event, value=url: open_source_reference(value),
                )
                source_row += 1

        info_frame = ttk.LabelFrame(info_tab, text="Best Practice / How to", padding=10)
        info_frame.grid(row=0, column=0, pady=(0, 8), sticky="ew")
        info_frame.columnconfigure(0, weight=1)
        ttk.Label(
            info_frame,
            text=BEST_PRACTICE_TEXT,
            wraplength=650,
            justify="left",
        ).grid(row=0, column=0, sticky="ew")

        about_frame = ttk.LabelFrame(about_tab, text="About MarbleScape", padding=10)
        about_frame.grid(row=0, column=0, pady=(0, 8), sticky="ew")
        about_frame.columnconfigure(0, weight=1)
        ttk.Label(
            about_frame,
            text="MarbleScape",
            font=("TkDefaultFont", 13, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            about_frame, text=f"Version {VERSION}"
        ).grid(row=1, column=0, pady=(2, 8), sticky="w")
        ttk.Label(
            about_frame,
            text=("Satellite live imagery for your desktop. Satellite source images "
                  "can contain visible seams, missing scans, partial coverage and "
                  "other acquisition or processing artifacts."),
            wraplength=620, justify="left",
        ).grid(row=2, column=0, pady=(0, 10), sticky="w")
        about_actions = ttk.Frame(about_frame)
        about_actions.grid(row=3, column=0, sticky="w")
        ttk.Button(
            about_actions,
            text="Open GitHub project",
            command=lambda: webbrowser.open(PROJECT_URL, new=2),
        ).grid(row=0, column=0, padx=(0, 6))
        update_status_var = tk.StringVar(value="Update status has not been checked.")
        latest_release_url = {"value": None}

        def open_latest_release():
            url = latest_release_url["value"]
            if url:
                webbrowser.open(url, new=2)

        latest_release_button = ttk.Button(
            about_actions,
            text="Open latest release",
            command=open_latest_release,
        )
        latest_release_button.grid(row=0, column=2)
        latest_release_button.state(["disabled"])

        def check_for_updates():
            check_update_button.state(["disabled"])
            latest_release_button.state(["disabled"])
            update_status_var.set("Checking GitHub for updates...")

            def finish(result=None, error=None):
                check_update_button.state(["!disabled"])
                if error:
                    update_status_var.set(str(error))
                    return
                latest_release_url["value"] = result["url"]
                latest_release_button.state(["!disabled"])
                if result["update_available"]:
                    update_status_var.set(
                        f"Update available: {result['latest']} (installed: {VERSION})."
                    )
                else:
                    update_status_var.set(
                        f"MarbleScape is up to date ({VERSION}); latest public version: "
                        f"{result['latest']}."
                    )

            def worker():
                try:
                    result = check_github_update()
                    try:
                        root.after(0, lambda: finish(result=result))
                    except tk.TclError:
                        pass
                except Exception as exc:
                    try:
                        root.after(0, lambda value=str(exc): finish(error=value))
                    except tk.TclError:
                        pass

            threading.Thread(
                target=worker, name="MarbleScape-update-check", daemon=True
            ).start()

        check_update_button = ttk.Button(
            about_actions,
            text="Check for updates",
            command=check_for_updates,
        )
        check_update_button.grid(row=0, column=1, padx=(0, 6))
        ttk.Label(
            about_frame,
            textvariable=update_status_var,
            wraplength=620,
            justify="left",
        ).grid(row=4, column=0, pady=(8, 0), sticky="w")

        button_frame = ttk.Frame(container)
        button_frame.grid(row=1, column=0, sticky="ew")
        button_frame.columnconfigure(0, weight=1)
        ttk.Label(
            button_frame,
            textvariable=status_variables["activity"],
        ).grid(
            row=0,
            column=0,
            padx=(0, 12),
            sticky="w",
        )

        next_check_frame = ttk.Frame(button_frame)
        next_check_frame.grid(row=0, column=1, padx=(0, 12), sticky="e")
        ttk.Label(next_check_frame, text="Next check:").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            next_check_frame,
            textvariable=status_variables["next_check"],
            # Reserve room for a timestamp plus an explicit UTC offset so status
            # changes do not move the action buttons.
            width=31,
            anchor="w",
        ).grid(row=0, column=1, padx=(6, 0), sticky="w")

        download_status_frame = ttk.Frame(button_frame)
        download_status_frame.grid(
            row=1, column=0, columnspan=5, pady=(5, 0), sticky="ew"
        )
        download_status_frame.columnconfigure(0, weight=1)
        button_frame.rowconfigure(1, minsize=22)
        download_status_label = ttk.Label(
            download_status_frame,
            textvariable=status_variables["download"],
            anchor="w",
        )
        download_status_label.grid(row=0, column=0, padx=(0, 12), sticky="ew")
        completion_status = tk.StringVar(value="")
        ttk.Label(download_status_frame, textvariable=completion_status).grid(
            row=0, column=1, padx=(0, 10), sticky="w"
        )
        download_progress_value = tk.DoubleVar(value=0.0)
        download_progress_bar = ttk.Progressbar(
            download_status_frame,
            variable=download_progress_value,
            maximum=100.0,
            length=210,
            mode="determinate",
        )
        download_progress_bar.grid(row=0, column=2, sticky="e")
        download_progress_bar.grid_remove()

        def cancel_download():
            if DOWNLOAD_PROGRESS.request_cancel():
                status_variables["download"].set("Cancelling download...")
                cancel_download_button.state(["disabled"])

        cancel_download_button = ttk.Button(
            download_status_frame,
            text="Cancel download",
            command=cancel_download,
        )
        cancel_download_button.grid(row=0, column=3, padx=(10, 0), sticky="e")
        cancel_download_button.state(["disabled"])

        def refresh_download_status():
            show_speed = bool(variables["show_download_speed"].get())
            show_progress = bool(variables["show_download_progress"].get())
            show_bar = bool(variables["show_download_progress_bar"].get())
            keep_completed_visible = bool(
                variables["keep_completed_download_visible"].get()
            )
            snapshot = DOWNLOAD_PROGRESS.snapshot(
                keep_completed_visible=keep_completed_visible
            )
            completion_status.set(download_completion_text(snapshot))
            if (
                snapshot["active"]
                and snapshot["cancellable"]
                and not snapshot["cancel_requested"]
            ):
                cancel_download_button.state(["!disabled"])
            else:
                cancel_download_button.state(["disabled"])
            if (
                snapshot["active"]
                and snapshot["cancel_requested"]
                and not (show_speed or show_progress)
            ):
                status_variables["download"].set("Cancelling download...")
            elif (
                snapshot["cancelled"]
                and snapshot["visible"]
                and not (show_speed or show_progress)
            ):
                status_variables["download"].set("Download cancelled.")
            elif snapshot["visible"] and (show_speed or show_progress):
                status_variables["download"].set(format_download_progress(
                    snapshot,
                    show_speed=show_speed,
                    speed_unit=variables["download_speed_unit"].get(),
                    show_progress=show_progress,
                ))
            else:
                status_variables["download"].set("")
            if snapshot["visible"] and show_bar:
                download_progress_bar.grid()
                if snapshot["percent"] is None:
                    download_progress_bar.configure(mode="indeterminate")
                    download_progress_value.set((time.monotonic() * 35.0) % 100.0)
                else:
                    download_progress_bar.configure(mode="determinate")
                    download_progress_value.set(snapshot["percent"])
            else:
                download_progress_bar.grid_remove()
                download_progress_value.set(0.0)
            root.after(200, refresh_download_status)

        refresh_download_status()

        def save_settings(close_after=False):
            raw_values = {
                key: variable.get()
                for key, variable in variables.items()
            }
            raw_values.pop("output_device")
            raw_values.update(global_output_draft)
            raw_values["position"] = global_position_draft["value"]
            raw_values["monitor_positions"] = dict(monitor_positions_draft)
            raw_values["monitor_output_settings"] = deepcopy(monitor_outputs_draft)
            try:
                requested_source, requested_profiles = source_settings.get_selection()
                requested_copernicus_auth = source_settings.get_copernicus_auth()
                time_zone_label = raw_values["time_zone"]
                if time_zone_label not in time_zone_label_to_value:
                    raise ValueError("Display time zone is invalid.")
                raw_values["time_zone"] = time_zone_label_to_value[time_zone_label]
                preset_label = raw_values.pop("view_preset")
                if preset_label not in preset_label_to_value:
                    raise ValueError("View preset is invalid.")
                raw_values["view_preset"] = preset_label_to_value[preset_label]

                raw_values.pop("satellite_layer")
                requested_layer = requested_profiles["eumetsat"]["layer"]
                if (
                    requested_layer is None
                    and requested_layer != saved_layer_state["value"]
                ):
                    raise ValueError("Satellite layer cannot be empty.")

                updates = normalize_settings_form_values(raw_values, provider=requested_source)
                requested_library = profile_settings.get_library()
                requested_profile_columns = profile_settings.get_visible_columns()
                image_snapshot = capture_image_form(updates)
                startup_before_save = is_windows_startup_enabled()
                requested_startup = bool(raw_values["start_with_windows"])
                startup_changed = requested_startup != startup_before_save
                if startup_changed:
                    startup_snapshot = capture_windows_startup_state()
                    set_windows_startup_enabled(requested_startup)

                try:
                    def transform_configuration(text):
                        updated = replace_toml_values(
                            ensure_download_configuration_section(
                                ensure_display_configuration_section(text)
                            ),
                            updates,
                        )
                        if image_form_state["loaded"]:
                            updated = replace_image_settings(updated, image_snapshot)
                        else:
                            updated = replace_source_configuration(
                                updated,
                                requested_source,
                                requested_profiles,
                                bool(raw_values["check_for_source_updates"]),
                            )
                        if not image_form_state["loaded"] and requested_source == "eumetsat" and requested_layer != saved_layer_state["value"]:
                            updated = replace_primary_wms_layer_name(
                                updated,
                                requested_layer,
                            )
                        updated = replace_copernicus_auth_configuration(
                            updated, requested_copernicus_auth
                        )
                        updated = ensure_profile_list_configuration_section(updated)
                        return replace_toml_section_value(
                            updated, "profile_list", "visible_columns",
                            list(requested_profile_columns),
                        )

                    changed = update_active_configuration_and_profiles(
                        transform_configuration, requested_library
                    )
                except Exception as config_error:
                    if startup_changed:
                        try:
                            restore_windows_startup_state(startup_snapshot)
                        except Exception as rollback_error:
                            raise RuntimeError(
                                f"{config_error} Windows startup rollback also "
                                f"failed: {rollback_error}"
                            ) from config_error
                    raise
            except Exception as exc:
                messagebox.showerror(
                    "Unable to save settings",
                    str(exc),
                    parent=root,
                )
                return

            if changed:
                request_runtime_configuration_reload(icon)
            else:
                icon.update_menu()
            if requested_source == "eumetsat":
                saved_layer_state["value"] = requested_layer
            image_form_state.update(base=image_snapshot, loaded=False)
            if close_after:
                root.destroy()

        ttk.Button(button_frame, text="Apply", command=save_settings).grid(
            row=0, column=2, padx=(0, 8)
        )
        ttk.Button(
            button_frame,
            text="OK",
            command=lambda: save_settings(close_after=True),
        ).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(button_frame, text="Cancel", command=root.destroy).grid(
            row=0, column=4
        )

        root.protocol("WM_DELETE_WINDOW", root.destroy)

        def cancel_dialog_timers(event):
            if event.widget is root:
                # Each dialog owns a Tk interpreter. Cancel pending status and
                # focus callbacks before their Python commands are destroyed.
                for timer in root.tk.call("after", "info"):
                    root.tk.call("after", "cancel", timer)

        root.bind("<Destroy>", cancel_dialog_timers, add="+")
        def bind_page_scrolling(widget):
            # Handle wheel input before combobox class bindings can change a value.
            # Native dropdown popups keep their own scrolling bindings.
            widget.bindtags((scroll_tag, *widget.bindtags()))
            for child in widget.winfo_children():
                bind_page_scrolling(child)

        for page in notebook.tabs():
            bind_page_scrolling(root.nametowidget(page))
        for section in (
            preset_frame, generic_view_frame, wallpaper_frame, display_time_frame,
            output_device_frame, output_frame,
            update_frame, download_display_frame, download_retry_frame, catalogue_retry_frame,
            latest_folder_frame, history_frame, status_frame, cache_frame,
        ):
            section.columnconfigure(1, weight=1)
        root.update_idletasks()
        # Center within the primary monitor's usable area, excluding the taskbar.
        left, top = 0, 0
        right, bottom = root.winfo_screenwidth(), root.winfo_screenheight()
        if os.name == "nt":
            from ctypes import wintypes
            work_area = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0):
                left, top, right, bottom = work_area.left, work_area.top, work_area.right, work_area.bottom
        scale = max(1.0, float(root.tk.call("tk", "scaling")) / (96 / 72))
        usable_width = max(1, right - left - round(32 * scale))
        usable_height = max(1, bottom - top - round(64 * scale))
        window_width = min(round(800 * scale), usable_width)
        window_height = min(round(700 * scale), usable_height)
        root.minsize(min(round(680 * scale), usable_width), min(round(420 * scale), usable_height))
        window_x = left + max(0, (right - left - window_width) // 2)
        window_y = top + max(0, (bottom - top - window_height - round(32 * scale)) // 2)
        root.geometry(f"{window_width}x{window_height}+{window_x}+{window_y}")
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
        root.after(250, lambda: root.attributes("-topmost", False))
        try:
            root.mainloop()
        finally:
            source_settings.close()
            profile_settings.close()

    def open_settings_dialog(icon, item):
        del item
        with settings_dialog_lock:
            if settings_dialog_state["open"]:
                return
            settings_dialog_state["open"] = True

        def settings_worker():
            try:
                run_settings_dialog(icon)
            except Exception as exc:
                show_tray_error(icon, "Unable to open settings", exc)
            finally:
                with settings_dialog_lock:
                    settings_dialog_state["open"] = False

        threading.Thread(
            target=settings_worker,
            name="MarbleScapeSettings",
            daemon=True,
        ).start()

    def run_backup_export_dialog():
        import tkinter as tk
        from tkinter import filedialog, messagebox

        root = create_tray_dialog_root(tk)
        apply_tk_window_icon(root)
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        try:
            selected_path = filedialog.asksaveasfilename(
                parent=root,
                title="Export MarbleScape settings backup",
                initialdir=str(SCRIPT_DIR),
                initialfile=(
                    "marblescape-settings-"
                    f"{dt.datetime.now():%Y-%m-%d_%H%M%S}.json"
                ),
                defaultextension=".json",
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not selected_path:
                return
            saved_path = export_settings_backup(selected_path)
            messagebox.showinfo(
                "Settings backup exported",
                f"Backup saved to:\n{saved_path}",
                parent=root,
            )
        finally:
            root.destroy()

    def open_backup_export_dialog(icon, item):
        del item
        with settings_dialog_lock:
            if settings_dialog_state["open"]:
                return
            settings_dialog_state["open"] = True

        def backup_export_worker():
            try:
                run_backup_export_dialog()
            except Exception as exc:
                show_tray_error(icon, "Unable to export settings backup", exc)
            finally:
                with settings_dialog_lock:
                    settings_dialog_state["open"] = False

        threading.Thread(
            target=backup_export_worker,
            name="MarbleScapeBackupExport",
            daemon=True,
        ).start()

    def run_backup_import_dialog(icon):
        import tkinter as tk
        from tkinter import filedialog, messagebox

        root = create_tray_dialog_root(tk)
        apply_tk_window_icon(root)
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        config_text = None
        profile_library = None
        startup_enabled = None
        try:
            selected_path = filedialog.askopenfilename(
                parent=root,
                title="Import MarbleScape settings backup",
                initialdir=str(SCRIPT_DIR),
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not selected_path:
                return

            try:
                config_text, profile_library, startup_enabled = import_settings_backup(
                    selected_path
                )
            except Exception as exc:
                messagebox.showerror(
                    "Unable to import backup",
                    str(exc),
                    parent=root,
                )
                return

            confirmed = messagebox.askyesno(
                "Import settings backup",
                "Importing this backup replaces the current configuration "
                "and applies it immediately. Continue?",
                parent=root,
                icon="warning",
            )
            if not confirmed:
                return
        finally:
            root.destroy()

        apply_settings_backup(icon, config_text, profile_library, startup_enabled)

    def open_backup_import_dialog(icon, item):
        del item
        with settings_dialog_lock:
            if settings_dialog_state["open"]:
                return
            settings_dialog_state["open"] = True

        def backup_import_worker():
            try:
                run_backup_import_dialog(icon)
            except Exception as exc:
                show_tray_error(icon, "Unable to import settings backup", exc)
            finally:
                with settings_dialog_lock:
                    settings_dialog_state["open"] = False

        threading.Thread(
            target=backup_import_worker,
            name="MarbleScapeBackupImport",
            daemon=True,
        ).start()

    def run_background_color_picker(icon):
        import tkinter as tk
        from tkinter import colorchooser

        root = create_tray_dialog_root(tk)
        apply_tk_window_icon(root)
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        try:
            selected = colorchooser.askcolor(
                color=normalize_background_color(BACKGROUND_COLOR),
                title="Choose MarbleScape background color",
                parent=root,
            )[1]
        finally:
            root.destroy()

        if selected:
            apply_configuration_updates(
                icon,
                "Unable to change background color",
                (
                    (
                        "output",
                        "background_color",
                        normalize_background_color(selected),
                    ),
                ),
            )

    def open_background_color_picker(icon, item):
        del item
        with settings_dialog_lock:
            if settings_dialog_state["open"]:
                return
            settings_dialog_state["open"] = True

        def color_picker_worker():
            try:
                run_background_color_picker(icon)
            except Exception as exc:
                show_tray_error(icon, "Unable to open color picker", exc)
            finally:
                with settings_dialog_lock:
                    settings_dialog_state["open"] = False

        threading.Thread(
            target=color_picker_worker,
            name="MarbleScapeColorPicker",
            daemon=True,
        ).start()

    def run_custom_render_factor_dialog(icon):
        import tkinter as tk
        from tkinter import simpledialog

        root = create_tray_dialog_root(tk)
        apply_tk_window_icon(root)
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        try:
            output_width, output_height = get_output_dimensions()
            initial_factor = max(
                1.0,
                get_requested_render_scale(output_width, output_height),
            )
            selected = simpledialog.askfloat(
                "Custom render quality",
                "Render quality factor (minimum 1.0):",
                initialvalue=initial_factor,
                minvalue=1.0,
                parent=root,
            )
        finally:
            root.destroy()

        if selected is None:
            return
        if not math.isfinite(selected) or selected < 1.0:
            raise ValueError("Render quality factor must be at least 1.0.")
        apply_configuration_updates(
            icon,
            "Unable to change render quality",
            (("output", "render_scale", selected),),
        )

    def open_custom_render_factor_dialog(icon, item):
        del item
        with settings_dialog_lock:
            if settings_dialog_state["open"]:
                return
            settings_dialog_state["open"] = True

        def render_factor_worker():
            try:
                run_custom_render_factor_dialog(icon)
            except Exception as exc:
                show_tray_error(icon, "Unable to set render quality", exc)
            finally:
                with settings_dialog_lock:
                    settings_dialog_state["open"] = False

        threading.Thread(
            target=render_factor_worker,
            name="MarbleScapeRenderQuality",
            daemon=True,
        ).start()

    def run_custom_resolution_dialog(icon):
        import tkinter as tk
        from tkinter import simpledialog

        configured_height = 0 if HEIGHT is None else HEIGHT
        root = create_tray_dialog_root(tk)
        apply_tk_window_icon(root)
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        try:
            selected = simpledialog.askstring(
                "Custom resolution",
                "Resolution as WIDTH x HEIGHT (use height 0 for automatic):",
                initialvalue=f"{WIDTH} x {configured_height}",
                parent=root,
            )
        finally:
            root.destroy()

        if selected is None:
            return

        automatic_ratio = ASPECT_RATIO
        if automatic_ratio is None and WIDTH and HEIGHT:
            automatic_ratio = f"{WIDTH}:{HEIGHT}"
        width, height, aspect_ratio = parse_resolution_text(
            selected,
            automatic_ratio,
        )
        apply_configuration_updates(
            icon,
            "Unable to change resolution",
            (
                ("output", "width", width),
                ("output", "height", height),
                ("output", "aspect_ratio", aspect_ratio),
            ),
        )

    def open_custom_resolution_dialog(icon, item):
        del item
        with settings_dialog_lock:
            if settings_dialog_state["open"]:
                return
            settings_dialog_state["open"] = True

        def resolution_worker():
            try:
                run_custom_resolution_dialog(icon)
            except Exception as exc:
                show_tray_error(icon, "Unable to set custom resolution", exc)
            finally:
                with settings_dialog_lock:
                    settings_dialog_state["open"] = False

        threading.Thread(
            target=resolution_worker,
            name="MarbleScapeResolution",
            daemon=True,
        ).start()

    def custom_resolution_is_selected(item):
        del item
        try:
            actual_width, actual_height = get_output_dimensions()
        except (TypeError, ValueError):
            return True

        return not any(
            actual_width == width
            and actual_height == height
            for _label, width, height in OUTPUT_SIZE_MENU_CHOICES
        )

    def run_custom_aspect_ratio_dialog(icon):
        import tkinter as tk
        from tkinter import simpledialog

        initial_ratio = ASPECT_RATIO
        if initial_ratio is None and WIDTH and HEIGHT:
            initial_ratio = f"{WIDTH}:{HEIGHT}"

        root = create_tray_dialog_root(tk)
        apply_tk_window_icon(root)
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        try:
            selected = simpledialog.askstring(
                "Custom aspect ratio",
                "Aspect ratio (for example 16:10 or 2.35):",
                initialvalue=str(initial_ratio),
                parent=root,
            )
        finally:
            root.destroy()

        if selected is None:
            return

        aspect_ratio = normalize_aspect_ratio_text(selected)
        apply_configuration_updates(
            icon,
            "Unable to change aspect ratio",
            (
                ("output", "aspect_ratio", aspect_ratio),
                ("output", "height", 0),
            ),
        )

    def open_custom_aspect_ratio_dialog(icon, item):
        del item
        with settings_dialog_lock:
            if settings_dialog_state["open"]:
                return
            settings_dialog_state["open"] = True

        def aspect_ratio_worker():
            try:
                run_custom_aspect_ratio_dialog(icon)
            except Exception as exc:
                show_tray_error(icon, "Unable to set custom aspect ratio", exc)
            finally:
                with settings_dialog_lock:
                    settings_dialog_state["open"] = False

        threading.Thread(
            target=aspect_ratio_worker,
            name="MarbleScapeAspectRatio",
            daemon=True,
        ).start()

    def custom_aspect_ratio_is_selected(item):
        del item
        try:
            current_ratio = parse_aspect_ratio(ASPECT_RATIO)
        except (TypeError, ValueError):
            return True
        return not any(
            math.isclose(
                current_ratio,
                parse_aspect_ratio(aspect_ratio),
                rel_tol=1e-9,
            )
            for _label, aspect_ratio in ASPECT_RATIO_MENU_CHOICES
        )

    def custom_render_factor_is_selected(item):
        del item
        return not any(
            values_match(get_render_scale_setting(), factor)
            for _label, factor in RENDER_QUALITY_MENU_CHOICES
        )

    def startup_is_enabled(item):
        del item
        return is_windows_startup_enabled()

    def profile_rotation_is_enabled(item):
        del item
        return bool(IMAGE_PROFILE_LIBRARY.get("rotation", {}).get("enabled"))

    def toggle_profile_rotation(icon, item):
        del item
        try:
            library = deepcopy(IMAGE_PROFILE_LIBRARY)
            if not library.get("items"):
                raise ValueError(
                    "Add at least one image profile before enabling rotation."
                )
            rotation = library.setdefault("rotation", {})
            rotation["enabled"] = not bool(rotation.get("enabled"))
            changed = update_profile_library_file(library)
            if changed:
                request_runtime_configuration_reload(icon)
        except Exception as exc:
            show_tray_error(icon, "Unable to change profile rotation", exc)

    def toggle_windows_startup(icon, item):
        del item
        try:
            set_windows_startup_enabled(not is_windows_startup_enabled())
            icon.update_menu()
        except Exception as exc:
            try:
                icon.notify(str(exc), "Unable to change Windows startup")
            except Exception:
                log(f"Unable to change Windows startup: {exc}")

    def update_tray_status(state, next_check):
        with tray_status_lock:
            tray_status["state"] = state
            tray_status["next_check"] = next_check
        try:
            tray_icon.update_menu()
        except Exception as exc:
            log(f"Tray menu refresh warning: {exc}")

    def update_activity_status_text(item):
        del item
        state, _next_check = get_tray_status_snapshot()

        if state == "fetching":
            return "↻ Fetching new image..."
        if state == "checking":
            return "Checking for new image..."
        if IMAGE_SOURCE != "eumetsat":
            with IMAGE_STATUS_LOCK:
                unavailable = bool(IMAGE_STATUS["error"])
            if unavailable:
                return "Source unavailable - keeping previous image"
        return "Standing by..."

    def force_loading_new_picture(icon, item):
        del item
        FORCE_UPDATE_EVENT.set()
        log("Manual image download requested.")
        icon.update_menu()

    def force_loading_is_enabled(item):
        del item
        state, _next_check = get_tray_status_snapshot()
        return (
            state == "waiting"
            and not FORCE_UPDATE_EVENT.is_set()
            and not APPLICATION_STOP_EVENT.is_set()
        )

    def next_check_status_text(item):
        del item
        return f"Next check: {format_next_check_status()}"

    tray_icon = pystray.Icon(
        "MarbleScape",
        create_windows_tray_image(),
        "MarbleScape - Satellite live imagery for your desktop.",
        menu=pystray.Menu(
            pystray.MenuItem("Open image folder", open_output_folder),
            pystray.MenuItem(
                "Settings...",
                open_settings_dialog,
                default=True,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Profile rotation",
                toggle_profile_rotation,
                checked=profile_rotation_is_enabled,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Force loading new picture",
                force_loading_new_picture,
                enabled=force_loading_is_enabled,
            ),
            pystray.MenuItem(
                update_activity_status_text,
                None,
                enabled=False,
            ),
            pystray.MenuItem(
                next_check_status_text,
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Support this project...",
                open_support_dialog,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start with Windows",
                toggle_windows_startup,
                checked=startup_is_enabled,
            ),
            pystray.MenuItem("Restart", restart_application),
            pystray.MenuItem("Exit", exit_application),
        ),
    )

    def application_worker():
        try:
            result["exit_code"] = run_application(
                argv,
                pause_on_error=False,
                configuration_loaded=True,
                status_callback=update_tray_status,
            )
        except SystemExit as exc:
            result["exit_code"] = int(exc.code or 0)
        finally:
            tray_icon.stop()

    worker = threading.Thread(
        target=application_worker,
        name="MarbleScapeWorker",
        daemon=True,
    )

    def tray_setup(icon):
        icon.visible = True
        worker.start()
        warm_public_catalogues()
        check_for_startup_update()

    tray_icon.run(setup=tray_setup)
    APPLICATION_STOP_EVENT.set()
    worker.join(timeout=2.0)
    return result["exit_code"]


# =============================================================================
# PROGRAM ENTRY POINT
# =============================================================================


if __name__ == "__main__":
    restart_wait = 15 if os.environ.get("MARBLESCAPE_RESTART_WAIT") == "1" else 0
    if not acquire_windows_single_instance(wait_seconds=restart_wait):
        log("MarbleScape is already running.")
        sys.exit(0)
    if should_use_windows_tray():
        sys.exit(run_with_windows_tray())
    sys.exit(run_application())
