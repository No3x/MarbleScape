# User guide

Settings, profiles, rotation, backups, storage, and configuration reference.

[Back to the main README](../README.md)

## Best Practice / How to

The same guidance is available inside MarbleScape under **Info**.

The easiest way to create the satellite view you want is to begin with the
original browser for that image source. Open the source website from the
**Sources** tab in MarbleScape, explore its available views, and adjust the
visualization until the imagery matches your intended result. This gives you a
clear preview of the source data before you configure the wallpaper.

Then transfer the relevant choices to MarbleScape: image source, satellite,
mission, product, layer, projection, area, custom latitude and longitude, zoom,
date, coverage options, and any other source-specific settings. Configure the
output width, height, aspect ratio, fit mode, and wallpaper position for your
monitor. Select **Apply**, review the resulting wallpaper, and refine the
settings if necessary. Once the result is satisfactory, save the current Image
settings as a profile under **Profiles & Rotation** so the same view can be
restored or included in a rotation.

For cleaner edges, finer details, and fewer visible stair-step artifacts,
choose a source resolution one available size above the required output when
bandwidth and provider limits allow it. **Automatic** remains the efficient
starting point.

Provider websites and MarbleScape can use slightly different labels or expose
different subsets of the same catalogue. Use the actual satellite view and
geographic result as the reference when matching settings. Satellite imagery
can contain seams, processing artifacts, and areas with missing or partial
imagery.

## Windows tray menu

During normal continuous operation, a MarbleScape icon appears in the Windows system tray area. Its menu provides access to:

- the current image folder;
- the settings dialog for common image, wallpaper and history options;
- a forced image refresh and current activity status;
- per-user Windows startup;
- restart and exit.

`Open image folder` appears directly above `Settings...` in the first menu
section. **Profile rotation** can be switched on or off in its own tray section
above **Force loading new picture**. **Support this project...** opens a small
dialog with monochrome buttons for starring the GitHub repository, Ko-fi, and
Buy Me a Coffee. Changes made in Settings are written to the active local TOML file and
applied by the running application without restarting it. A new image is
requested when required. Double-clicking the tray icon opens Settings.
Diagnostic and one-shot commands do not show a tray icon. `Restart` remains
available for troubleshooting.

The lower menu shows `Checking for new image...`, `Fetching new image...`,
or `Standing by...`, followed by `Next check` in the selected display time zone.
`Start with Windows` is in the bottom section above `Restart`.

**Force loading new picture** is available both in the tray menu and in
Settings under **Image**. While MarbleScape is standing by, either control
requests an immediate download even when the provider timestamp has not
changed. Identical content does not create a duplicate history image.

## Settings

In Settings, the read-only `Status and storage` section displays the current
image size, latest image count, estimated history count, maximum total image
count, estimated storage, current latest/cache/history image-file usage, and next
check time. Usage is shown in MB, switching to GB at 1,000 MB. Estimates use
the saved, active configuration and current image size; unsaved edits do not
affect them. Time-based retention estimates assume a new image each cycle.

Settings is organized into nine tabs:

- **General:** Wallpaper, global display time zone, and Updates.
- **Image:** Source, source-specific image selection, Force loading new picture, View, and Output.
- **Download:** Transfer speed unit, progress text, progress-bar visibility,
  and completed-status retention.
- **Profiles & Rotation:** Named Image snapshots, their order, and the rotation interval.
- **Storage & History:** Latest and History folders, retention, and current storage status.
- **Backup:** JSON import and export.
- **Sources:** Clickable links to the satellite imagery viewers used by MarbleScape,
  grouped by provider. Technical API and data endpoints are not listed there.
- **Info:** Workflow and satellite imagery guidance.
- **About:** Installed version, project link, imagery notice, and a manual GitHub update check.

At tray startup, MarbleScape checks the newest public GitHub release without
delaying image updates. When it is newer than the installed version, a small
window offers **Skip this version** and **Open GitHub**. Skipping is stored in
the configuration and applies only to that version. The About tab also has a
manual check for the newest public release or tag. A private
repository cannot be checked by the application without GitHub authentication;
in that case About reports that no public version is accessible and the project
button can still open the repository in the user's signed-in browser.

General > **Date and time** provides **System time (recommended)** and **UTC**.
System time follows the Windows time zone and daylight-saving rules; displayed
local values include their effective UTC offset. This choice affects displayed
acquisition and next-check times only. Provider requests, comparisons, and
cached timestamps remain in UTC.

The resizable window opens centered at approximately 800 x 700 logical pixels,
adjusted for Windows scaling and the available desktop work area. Each tab
scrolls vertically when needed, using its scrollbar or the mouse wheel.
Keyboard navigation reveals focused settings automatically. Activity status,
the single-line **Next check** timestamp, and **Apply / OK / Cancel** stay
visible outside the scrolling area. The timestamp has reserved width to avoid
moving the buttons when its value changes.

During an image transfer, the footer can show the current download speed,
transferred size, percentage, and a progress bar. The Download tab controls
each part independently and offers Automatic, KB/s, MB/s, and Mbit/s for the
speed. An exact total and percentage are available when the server supplies a
valid `Content-Length` for the complete transfer. For dynamic tiled or
multi-request images, MarbleScape shows the transferred amount and an
indeterminate bar until the aggregate total is known. It does not make extra
requests merely to determine a size. **Keep completed download visible until
next download** retains the successful 100% result in the footer; when disabled,
the result disappears after a short delay. The adjacent **Completed.** label
appears only after the image is installed, rather than as soon as transferred
bytes happen to reach 100%. **Download retries** sets 1-9 retries after the
first attempt (2-10 total attempts) for temporary image-transfer failures;
invalid requests and authentication errors are reported without retrying.
**Catalogue retries** independently sets 1-9 retries after the first metadata
request. If every catalogue attempt fails, MarbleScape uses the most recent
catalogue data stored in `content/catalogues.json` when available.
At startup, MarbleScape checks public catalogues in the background. Complete
NOAA, Himawari, CIRA SLIDER, NASA Worldview, and EUMETSAT catalogues saved
within the previous 24 hours are reused without another metadata request.
Expired sources are checked separately. After an unsuccessful check, MarbleScape
waits one hour before another automatic attempt when a complete cache is
available. Manual catalogue refreshes still check online immediately.
The latest image is checked separately on each image update. For
catalogue pages that provide an ETag or Last-Modified value, it uses a
conditional request and reuses unchanged responses from
`content/catalogue_http.json`. Sources without these validators still require
a full metadata response. Copernicus checks only dates since the latest cached
date, including a 14-day overlap for late additions. A manual Copernicus
catalogue refresh still checks the complete available date range.
**Cancel download** is enabled in
the fixed footer while an image transfer can still be stopped. Cancelling keeps
the current wallpaper, discards the unfinished result, and creates no History
or profile-cache entry. A cancelled rotation download does not use one of its
three failure attempts; that profile becomes eligible again at the next regular
rotation interval.

The Image tab selects the imagery source: **EUMETSAT**, **NOAA GOES**,
**Solar (SUVI)**, **Himawari**, **CIRA SLIDER**, **NASA Worldview**, or **Copernicus Browser**.
Within NOAA GOES, the **Satellite** dropdown selects GOES-East or GOES-West.
Each satellite has its own area, product, and resolution settings. For NOAA,
Himawari, and SLIDER sources, choose a category/satellite, area/sector,
product/layer, and source resolution
from dependent dropdowns.
Only choices offered for the selected area and product are listed.
Solar uses the available SUVI wavelength products. Each source retains
its own image selection when switching sources.

For NASA Worldview, choose a layer category, imagery layer, latest or fixed
date, and render resolution. The default is the latest available VIIRS
NOAA-20 Corrected Reflectance True Color layer.

Available still-image sources default to a GeoColor/GeoColour visualization
where the provider offers one. Sources that use another name default to the
closest natural or true-color product. Solar remains on its wavelength product
because GeoColor does not apply to the Sun.

**General > Output device** lists connected monitors. Choose **All monitors** to
set the default Output values, then choose a display to set its own size, aspect
ratio, render quality, background color, and position. These values are saved by
monitor ID. If a display is disconnected, MarbleScape keeps its settings but
does not apply a wallpaper to it. The same display receives its saved settings
on the next wallpaper update after reconnection. **Do not update (keep current
wallpaper)** skips future updates for that display and leaves its current image
in place. **Restore previous wallpaper** immediately restores the image
MarbleScape saved before it first replaced the wallpaper on the selected
display and then stops future updates for that display. The saved copy is kept in
`content/previous_wallpapers`. If MarbleScape had already replaced the wallpaper
before this feature was installed, the older image cannot be recovered. For a
Windows slideshow, the saved copy is the image visible when MarbleScape first
updates the display, not the slideshow itself. Windows uses one system-wide
placement mode, so
switching to per-display placement may change how an existing wallpaper on a
skipped display is scaled. Individual render quality can only use detail already
present in the shared downloaded image. A display's background color fills
placement margins; background already rendered into the shared image remains
unchanged.

**General > Output** provides wallpaper resolution and aspect-ratio
presets. Selecting a preset fills the editable width, height, and ratio
fields; choose `Custom` or edit those fields directly for another size.
The NOAA, Himawari, or CIRA SLIDER source resolution controls the downloaded image, while **General > Output**
controls the resulting wallpaper dimensions. Fit mode, zoom, and background
color determine its framing. EUMETSAT projection, layer composition,
render quality, and TrueColor night controls apply only to EUMETSAT.
NASA Worldview's Render resolution controls the global GIBS WMS image supplied
before the same fit/crop, zoom, background, and Output settings are applied.

For NOAA, Himawari, and CIRA SLIDER, MarbleScape lists every safely renderable source resolution advertised by
the selected provider, including very large images. A listed resolution
confirms that the provider offers that variant, but it does not guarantee that
every download will finish: large transfers take longer and can be interrupted
by the image server, a network timeout, or a connection reset. If this happens
repeatedly, select a smaller source resolution and try again.

**Automatic (recommended)** is the default source/render resolution. With two
or more connected monitors, it uses the largest width and height needed by the
monitors receiving a wallpaper, including any larger per-monitor Output size.
This covers mixed landscape and portrait setups. A monitor set to **Do not update
(keep current wallpaper)** does not count. With one monitor, it uses the
configured Output size as before. The
smallest advertised source that satisfies this target, fit/crop mode, and zoom
is selected without enlarging visible source pixels. When no
exact size exists it selects the next sufficient size; when no listed size is
large enough it uses the largest one. Manual sizes and **Largest available**
remain selectable. This reduces bandwidth, memory use, and provider load while
retaining the detail the wallpaper can display.

EUMETSAT projection, fit mode, zoom, preset, and TrueColor night controls appear
in Image > Source directly below **Product / layer**. Selecting a preset fills
its satellite layer, projection, fit mode, and zoom defaults;
individual values can then be adjusted. **Apply** saves and activates changes
while keeping Settings open, **OK** applies and closes the window, and **Cancel**
discards changes made since the last Apply.

Image **Fit mode** determines framing inside the generated output file:
`fit` retains the view, while `crop` fills the output and trims edges.
General > Output > **Position** offers center, tile, stretch, fit, fill, span,
and **Do not update (keep current wallpaper)** for the selected output device.
MarbleScape prepares a monitor-sized image when positions differ between
displays. A position change uses
the existing local latest image without requesting a new source image or image
URL. It also works when the source is temporarily unavailable and leaves the
regular image-check schedule intact.

### Image profiles and rotation

In **Profiles & Rotation**, give the current Image settings a name and select
**Add current image**. Select an existing entry to update, rename, delete,
or load it into Image. **Apply profile** next to **Load into Image** saves and
activates the selected profile immediately and requests its image. Use Move
up/down to choose the rotation order. Double-click a profile to apply it
immediately.
These changes remain drafts until **Apply** or **OK**; Cancel discards drafts.
Loading a profile fills the Image form; Apply requests its image.

The profile list shows **Profile name**, **Source**, **Selection**, **Time**,
**Area / location**, **Lat**, **Long**, and **Coverage mode** in that order.
Scroll horizontally to see the rightmost columns in the normal-sized Settings
window. Copernicus shows the saved highlight or `Custom Lat/Long` in the area
column and places its coordinates in the separate Lat and Long columns. Other
sources show their area or preset and a dash for both coordinates. Coverage
mode shows the Copernicus selection and its
gap-fill lookback, or enabled EUMETSAT gap filling. A dash means the source has
no applicable coverage setting. The successfully applied rotation entry is
marked **Active**. Right-click a value to copy that cell or its complete row;
`Ctrl+C` copies the selected row as tab-separated text. Right-click a column
heading to show or hide individual columns. At least one column remains visible,
and heading separators can be dragged to resize columns without automatic
stretching them back during list or window updates. Column visibility is persistent,
and **Apply** or **OK** saves the selection in `marblescape_config.toml` under
`[profile_list]`. Latest
profiles show the acquisition time of their last successfully used provider
image in the global display time zone, for example
`Latest · YYYY-MM-DD HH:MM UTC+02:00`, or `Latest · not loaded yet` before their
first successful load. A fixed Copernicus, EUMETSAT, or NASA Worldview date appears as
`Fixed · YYYY-MM-DD`. **Selected profile details** below the list expands the
saved configuration, highlight, area or coordinates, product and layer, source
and output resolution, fit/zoom settings, global Windows wallpaper position,
the acquisition time explicitly in UTC, and cache status for the selected entry.

Profiles store the source and its selections, EUMETSAT layers and view,
fit/crop, zoom, output size, render quality, background color, and custom
latest folder. They also store whether that profile checks for newer imagery.
General and History settings remain shared. The library and
rotation settings are stored in the standalone `profiles.toml` in the
application root beside `marblescape.exe` (or the Python sources). Profiles are
included in the JSON settings backup.

Enable rotation and choose a positive interval in minutes, days, or weeks.
The first profile loads when rotation starts. After a successful load, its
full interval elapses before the next profile loads; normal image update
checks continue in between. Set wallpaper automatically must be enabled to
apply images to the Windows desktop. Failed profile loads use the global
**Download retries** limit, with a short retry delay. A download that exhausts
that limit skips the profile without multiplying its network attempts. If every
profile fails, the last wallpaper stays and rotation waits
one interval before trying the list again. Program restart begins with the
first profile; profile-list or interval changes restart the schedule.
One-shot and diagnostic commands do not run rotation.

Rotation images use a persistent content-addressed cache under
`content/cache`. A SQLite index maps each stable profile ID, its image-setting
signature, and the current provider frame signature to an immutable SHA-256
named PNG. Returning to an unchanged profile still checks lightweight provider
metadata, but it reuses the verified local PNG instead of downloading or
rendering the image again. Identical cached output can be shared by multiple
profiles. Changed image settings, changed source timestamps, missing files, or
failed integrity checks invalidate the entry. Deleted profiles are removed
from the index and unreferenced PNGs are pruned.

Image > **Image updates** can disable regular provider checks and downloads.
With checks disabled, MarbleScape downloads the selected latest image once only
when no verified local image exists for those exact image settings and output
dimensions. Later runs reuse that image without contacting the image provider.
The setting is saved independently in each image profile. **Force loading new
picture** deliberately bypasses this mode for one download.

**Storage & History > Profile image cache** reports its profile count, image
count, and size. **Clear cache** removes only profile-cache images and metadata;
it leaves normal Latest and History images intact. If a rotated profile is
active, MarbleScape requests a fresh image after clearing.

## Settings backups

Use the Backup tab in `Settings...` to create a JSON backup.
The backup contains the complete active TOML configuration, `profiles.toml`,
readable snapshots and integrity checksums for both files, and the current
per-user Windows startup state.

Import validates the JSON structure, checksum, and embedded TOML before
replacing and applying the active configuration. A confirmation is required.
Absolute output paths stored in a backup may need adjustment when restoring it
on another computer. A saved Copernicus Client secret is protected for the
Windows user that created it; enter that secret again after restoring on a
different computer or Windows account.

## Configuration

In Settings > Storage & History, use `Custom latest folder` under Latest image
folder or `Custom history folder` under History to type a path or select one
with `Choose...`. The buttons directly below open the respective target folder
in Explorer. Empty fields use
`content/latest` and `content/history` under the configured output root. Relative
paths use the application folder. Apply or OK applies the paths; missing
folders are created when needed. Latest and history must use different
directories and cannot point at the profile cache.

These paths are saved as `output.latest_folder` and `history.folder` and are
included in settings backups. `Open image folder` opens `content/latest` during
normal operation and the shared profile-cache folder while rotation is active.

General, source, image, download, history and storage settings are stored in
`marblescape_config.toml`. Named image profiles and rotation settings are stored
separately in `profiles.toml`. Both local files are ignored by the repository so
personal preferences are not accidentally published. The tracked
`marblescape_config.example.toml` contains neutral defaults.

Advanced options still require editing TOML: the EUMETSAT service endpoint,
archive timestamps, arbitrary WMS layer stacks/styles/opacities, custom
bounding boxes, plus the shared timeout and output-root paths. Restart after
manual file edits. Backup import applies a complete configuration, including
these advanced options.

`source.provider` selects `eumetsat`, `goes_east`, `goes_west`, `solar`,
`himawari`, `slider`, `worldview`, or `copernicus`.
The `sources.goes_east`, `sources.goes_west`, `sources.solar`, and
`sources.himawari`, `sources.slider`, and `sources.worldview` tables store each still-image source's `area`, `product`,
and `resolution`.
For Worldview, `area` is the GIBS layer ID and `product` is `latest` or a
fixed date offered by that layer.
`sources.copernicus` stores its catalogue selection, date, location, zoom,
maximum cloud cover, mosaic brightness, and map options; `[copernicus]` stores its OAuth Client ID
and protected secret. Switching providers retains each source's settings.
The `[download]` table stores the optional speed, size, percentage,
progress-bar, and completed-status retention preferences.

Relative output paths are resolved from the application directory, regardless
of the current working directory:

```toml
[output]
windows_root = "."                         # Application directory
# Alternatives (choose one value for windows_root):
# windows_root = "output"                  # An output subdirectory
# windows_root = 'D:\Images\MarbleScape'     # A custom absolute path
linux_root = "."
```

For a container, set `linux_root = "/output"` and mount that directory as a
volume.

When `height = 0`, the output height is calculated from `width` and
`aspect_ratio`.

### Render quality

For EUMETSAT, `Output > Render quality` provides:

- `Auto (max useful)` (default)
- `Standard (1.0×)`
- `High (1.25×)`
- `Very high (1.5×)`
- `Ultra (2.0×)`
- a custom factor of at least `1.0`

The final output resolution does not change. Higher settings request a larger
intermediate WMS image and resize it with Lanczos resampling. WMS requests are
capped at approximately 4000 pixels per axis. Outputs above that limit are
rendered proportionally within the service limit and then enlarged to the
selected size, so a quality factor above `1.0` adds no detail at 5K or 8K.
`Auto (max useful)` persists as an automatic mode and recalculates the largest
useful factor whenever the output resolution changes.
Pillow is required whenever the WMS render size differs from the final output.

### TrueColor day/night option

`Black TrueColor night side` applies only to `MTG TrueColor`. It displays
the sunlit portion of the satellite image and fills the unlit part of the Earth
disk with black. The area outside the disk retains the configured background
color.

### EUMETSAT catalogue, themes, missions, and layers

EUMETSAT uses dependent dropdowns in Image > Source for **Data theme**,
**Satellite / service**, **Mission**, **Product type**, and **Product / layer**.
Each choice restricts the following choices to combinations published together
in the official catalogue, so a mission, product type, or layer from another
service cannot remain selected accidentally. The public
EUMETSAT catalogue currently exposes MTG, MSG, Metop, multi-mission, and
Sentinel-3 choices. The theme filters are **Atmospheric composition**,
**Climate**, **Emergency**, **Marine**, and **Weather monitoring**; a product
may appear in more than one filter. The selectable channels, visualized
products, and RGB composites are derived from current official product metadata.
GeoColour is preferred when available. **Refresh EUMETSAT catalogue** reloads
this metadata, while **Refresh all catalogues** includes EUMETSAT together with
NOAA, Himawari, CIRA SLIDER, and NASA Worldview. Copernicus remains separate
because its acquisition catalogue depends on OAuth access and the selected
location.

For suitable LEO single-overpass layers, **Fill gaps with earlier imagery** can
place older passes underneath the newest image. The available maximum lookback
is **12 hours** or **24 hours**. MarbleScape downloads the newest image first and
only replaces its transparent No Data pixels; valid newest pixels always remain
on top. A failed optional older pass is skipped, while failure of the newest
image still fails the update normally. The resulting wallpaper can contain
several acquisition times.

This option is intentionally unavailable for GEO imagery and for products that
are already accumulated, daily, blended, climatological, or orbital-track
layers. Current eligible catalogue entries include Metop-A/B/C ASCAT and the
individual Sentinel-3A/B OLCI and SLSTR sea-surface-temperature products. Sparse
fire-detection layers are excluded because transparent pixels do not reliably
mean missing coverage there. Regional views are recommended: large global
reprojections require several WMS requests and can time out on the EUMETSAT
server even at a small output size.

Local composition and the Full Earth server path use the configured layer
order from bottom to top. The regional server path reverses this order, so
arbitrary stacks can look different when changing render mode or preset.
Friendly basemap and overlay names are
resolved by the application. Exact WMS product names can be discovered from the
live capabilities document:

```powershell
python marblescape_download.py --list-layers
python marblescape_download.py --export-layers marblescape_layers.json
```

Each `[[layers]]` entry supports `enabled`, `opacity`, `style`, and an
optional `time`. In `render_mode = "auto"`, a single server request is used
unless per-layer opacity or different timestamps require local composition.
The TrueColor black-night option also uses local composition with a separate
Earth mask, regardless of the selected render mode. EUMETSAT LEO gap filling
also forces local composition because each acquisition needs an independent
transparent WMS response.

### EUMETSAT projections

Select a projection in **Image > Source**, or using
`view.projection` in the configuration. Settings exposes every projection
published by the EUMETSAT viewer:

- `Geographic` (EPSG:4326)
- `GEOS: MSG FES, MTG FD` (geostationary, centered at 0 degrees)
- `GEOS: MSG RSS` (geostationary, centered at 9.5 degrees east)
- `GEOS: MSG IODC` (geostationary, centered at 41.5 degrees east)
- `Spherical Mercator` (EPSG:3857)
- `North Polar` (EPSG:3995)
- `South Polar` (EPSG:3976)

Projection is not part of the catalogue dependency chain. Current EUMETSAT
layers advertise the standard geographic, Mercator, and polar CRSs, while the
three geostationary views are generated by GeoServer. The EUMETSAT service can
therefore reproject a valid selected layer into every listed MarbleScape projection.
Projection changes the view and processing cost; it does not change the mission
or create coverage outside the source observations.

Areas outside the selected layer's image coverage may display the
basemap instead of satellite imagery. The short Settings hint reads:
"Coverage depends on the selected satellite layer."

Definitions follow the [EUMETSAT viewer configuration](https://view.eumetsat.int/assets/data/config.json).

Changing projection in the UI selects `full_earth`, resets zoom to `1.1`, and
uses `fit` (`crop` for Geographic, to stay within valid latitude/longitude
bounds). Here, Full Earth means the projection's default overview, not a
guarantee of global satellite coverage. The selected satellite layer is kept.
Settings are saved in the active TOML file and included in JSON backups.

Regional presets use Geographic; projected regional presets
are not included yet. For a custom projected view, specify `bbox` in meters
in the selected CRS. Geographic uses degrees. A projection cannot add missing
satellite coverage: some regions, especially the poles, may be blank with MTG.
Choose a layer that covers the area you want to display.
South Polar uses the Antarctic CRS EPSG:3976, not the Arctic EPSG:3995.
With MTG imagery, a black center and imagery only towards the edge can be
expected in polar views: the satellite does not observe the poles. In
particular, black-night masking also hides the underlying basemap. Selecting
a projection does not generate imagery for these missing areas.

### View presets

Available presets are:

- `full_earth`
- `europe`
- `mediterranean`
- `central_europe`
- `custom`

For `custom`, define `bbox` in logical x/y order. With the geographic
projection, use `[west, south, east, north]`. WMS 1.3.0 axis order is
handled automatically.

The default zoom is `1.1` for EUMETSAT, including its named presets and
projection changes. NOAA, Solar, Himawari, CIRA SLIDER, and NASA Worldview use
`1` when selected. An explicitly saved profile zoom is still respected.
