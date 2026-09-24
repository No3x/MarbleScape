# MarbleScape - Satellite live imagery for your desktop.

> MarbleScape is an unofficial third-party utility. It is not affiliated with or
> endorsed by EUMETSAT, NOAA, NICT, JMA, CIRA/CSU, NASA, or the Copernicus Data Space Ecosystem.

<p align="center">
  <img src="assets/examples/example_marblescape_globe_geocolor.webp" width="49%">
  <img src="assets/examples/example_marblescape_globe_truecolor.webp" width="49%">
</p>

<p align="center">
  <img src="assets/examples/example_marblescape_europe_geocolor.webp" width="49%">
  <img src="assets/examples/example_marblescape_europe_truecolor.webp" width="49%">
</p>

MarbleScape downloads current satellite imagery and prepares it as a desktop
wallpaper. It supports EUMETSAT, NOAA GOES and Solar/SUVI, Himawari, CIRA
SLIDER, NASA Worldview/GIBS, and the Copernicus Data Space. The Windows version
includes a system tray menu, automatic wallpaper updates, image profiles,
profile rotation, history, and local caching. The downloader also runs on Linux.

## Main features

- Current satellite imagery from seven source groups.
- Natural-color or GeoColor defaults where the provider offers them.
- Source-specific satellite, mission, area, layer, projection, date, and resolution controls.
- Automatic source-resolution selection based on connected displays when using multiple monitors.
- Named image profiles with configurable rotation.
- Latest-image storage, optional history, retention limits, and a profile cache.
- Download and catalogue retries with progress, speed, size, and cancellation controls.
- System-time or UTC timestamp display.
- JSON backup and restore for configuration and profiles.
- Windows tray operation, single-instance startup, and per-monitor wallpaper placement.
- A startup notice for new public GitHub releases, with a per-version skip option.

Satellite observations are not seamless photographic maps. Depending on the
provider and acquisition, imagery can contain clouds, scan seams, missing
coverage, day/night transitions, compression artifacts, or temporarily
inconsistent segments. See [Satellite imagery artifacts](docs/IMAGERY_ARTIFACTS.md).

## Quick start on Windows

A reviewed prebuilt package supports 64-bit Windows 10 and 11.

1. Download the versioned Windows archive (for example,
   `MarbleScape-windows-x64_v1.3.0.zip`) and `SHA256SUMS.txt` from
   [GitHub Releases](https://github.com/Gittegatt/MarbleScape/releases).
2. Verify the archive checksum and extract the complete ZIP to a user-writable folder.
3. Run `marblescape.exe`.
4. Open the MarbleScape notification-area menu and select `Settings...`.
5. Choose an image source, review the output settings, and select **Apply**.

The executable is not code-signed, so Windows may show an
unrecognized-publisher warning. Obtain it from a trusted release and verify its
checksum before continuing. Full instructions are in
[Installation and startup](docs/INSTALLATION.md).

## Run from source

MarbleScape requires Python 3.11 or newer and the packages listed in
`requirements.txt`.

```powershell
python -m pip install -r requirements.txt
python marblescape_download.py
```

Linux supports downloading and local composition without the Windows tray,
startup integration, or automatic wallpaper application:

```bash
python3 -m pip install -r requirements.txt
python3 marblescape_download.py --once
```

See [Installation and startup](docs/INSTALLATION.md) for command-line options,
continuous Linux operation, and tray-menu behavior.

## Best Practice / How to

The same guidance is available inside MarbleScape under **Info**.

1. Open the original viewer from MarbleScape's **Sources** tab.
2. Explore its satellite, mission, product, layer, projection, area, and date controls.
3. Transfer the useful choices to MarbleScape and configure **General > Output** for your monitor.
4. Select **Apply**, inspect the wallpaper, and refine the framing if needed.
5. Save the finished Image settings as a profile under **Profiles & Rotation**.

For cleaner edges and finer detail, choose a source resolution one available
size above the required output when bandwidth and provider limits allow it.
**Automatic** remains the efficient starting point. Provider websites and
MarbleScape may use different labels or expose different subsets of the same
catalogue, so compare the actual geographic result when matching settings.

## Image sources

| Source | Recommended use | Account required |
| --- | --- | --- |
| EUMETSAT | Europe, Africa, Atlantic, weather products, and multiple projections | No |
| NOAA GOES | Frequent full-disk and regional imagery for the Americas | No |
| Solar / Sun (SUVI) | Solar ultraviolet channels | No |
| Himawari | Asia-Pacific full-disk and regional imagery | No |
| CIRA SLIDER | Clean satellite products from several geostationary platforms | No |
| NASA Worldview | Global Earth imagery and broad scientific layer selection | No |
| Copernicus Browser | High-resolution regional Sentinel and Landsat imagery | Free OAuth client |

Still-image sources default to GeoColor, GeoColour, natural color, or true color
where available. Solar uses a wavelength channel because natural color does not
apply to the Sun. Copernicus rendering requires a free Sentinel Hub OAuth client
from the Copernicus Data Space account settings.
New Copernicus settings select Sentinel-2 Mosaics, Sentinel-2 Quarterly Mosaics,
and True Color Cloudless. Saved selections stay as configured.

Provider-specific satellites, missions, layers, projections, coverage modes,
cloud filtering, mosaic brightness, lookback behavior, and selection matrices are documented in
[Image source guide](docs/IMAGE_SOURCES.md).

## Settings overview

Settings contains these tabs:

- **General** - Wallpaper, display time zone, update timing, output device, and output size.
- **Image** - Source selection, image updates, and framing.
- **Download** - Transfer display, download retries, and catalogue retries.
- **Profiles & Rotation** - Saved Image configurations and rotation timing.
- **Storage & History** - Latest folder, History, retention, cache, and storage status.
- **Backup** - JSON export and import.
- **Sources** - Links to the original satellite imagery viewers.
- **Info** - Workflow and imagery guidance.
- **About** - Version, project link, imagery notice, and update check.

Settings changes remain drafts until **Apply** or **OK**. The footer keeps
activity, next-check time, download progress, and the main action buttons
visible while individual tabs scroll.

The detailed control reference, storage behavior, custom folders, advanced
TOML settings, render quality, presets, and backup format are in the
[User guide](docs/USER_GUIDE.md).

## Image profiles and rotation

Profiles save the current Image configuration, including provider selection,
view, output, source-specific settings, and whether newer imagery is checked.
Rotation can cycle through profiles at a selected interval while ordinary image
checks continue between profile changes.

The profile table displays Profile name, Source, Selection, Time,
Area/location, Lat, Long, and Coverage mode. Right-click a value to copy that
cell or the complete row. `Ctrl+C` copies the selected row as tab-separated
text. Right-click a column heading to show or hide columns. **Apply**, **OK**,
or **Apply profile** saves the selected columns in `marblescape_config.toml`.
Double-click a profile to apply it immediately.
Drag a heading separator to resize a column; manual widths remain stable while
the list refreshes or the Settings window changes size.
Profile definitions and rotation order are stored separately in `profiles.toml`.

See [User guide](docs/USER_GUIDE.md#image-profiles-and-rotation) for profile
timestamps, cache status, retry handling, and rotation behavior.

## Privacy and network access

MarbleScape contains no analytics, telemetry, advertising, or built-in API
keys. Configuration, downloaded images, history, and caches are stored locally.
It contacts the selected imagery providers and may refresh public catalogue
metadata in the background. Copernicus credentials are used only for direct
Copernicus Data Space requests; on Windows the saved Client secret is protected
for the current user with DPAPI.

The complete endpoint list, catalogue behavior, OAuth and quota information,
and Windows startup registration details are in
[Privacy and network access](docs/PRIVACY_AND_NETWORK.md).

## Documentation

- [Installation and startup](docs/INSTALLATION.md)
- [User guide](docs/USER_GUIDE.md)
- [Image source guide](docs/IMAGE_SOURCES.md)
- [Privacy and network access](docs/PRIVACY_AND_NETWORK.md)
- [Satellite imagery artifacts](docs/IMAGERY_ARTIFACTS.md)
- [Development and release builds](docs/DEVELOPMENT.md)
- [Data terms, attribution, and license](docs/LEGAL_AND_ATTRIBUTION.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## Support the project

[Star MarbleScape on GitHub](https://github.com/Gittegatt/MarbleScape)

[Support MarbleScape on Ko-fi](https://ko-fi.com/gittegatt)

[Support MarbleScape on Buy Me a Coffee](https://buymeacoffee.com/gittegatt)

## License and contact

MarbleScape is source-available under the
[PolyForm Noncommercial License 1.0.0](LICENSE). Required Notice: Copyright 2026
Gittegatt. Third-party components and imagery remain subject to their own terms.
See [Data terms, attribution, and license](docs/LEGAL_AND_ATTRIBUTION.md) before
publishing a binary or redistributing included visual material.

For project information and commercial licensing inquiries, visit the
[MarbleScape repository](https://github.com/Gittegatt/MarbleScape).
