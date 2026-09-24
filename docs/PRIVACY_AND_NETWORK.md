# Privacy and network access

Services contacted by MarbleScape, credential handling, quotas, and local startup registration.

[Back to the main README](../README.md)

## Privacy and network access

The application does not contain analytics, telemetry, advertising, or built-in
API keys. It stores configuration and downloaded images locally. User-provided
Copernicus OAuth credentials are used only for direct Copernicus Data Space API
requests as described below.

The Windows tray checks `api.github.com` for the latest public MarbleScape
release once after each start. The About tab can repeat the check manually.
Only release metadata is requested; no account login or OAuth credentials are
sent. The skipped release version is stored in `marblescape_config.toml`.

Network access depends on the selected source. EUMETSAT uses the WMS
endpoint configured in `marblescape_config.toml`, which defaults to:

```text
https://view.eumetsat.int/geoserver/wms
```

Its dependent theme, mission, product-type, and layer lists use the public
EUMETSAT product metadata at `view.eumetsat.int` and the EUMETSAT Product
Navigator API at `api.eumetsat.int`. No EUMETSAT account or API key is required.

GOES-East, GOES-West, and Solar/Sun use the official NOAA STAR catalog
and image hosts:

```text
https://www.star.nesdis.noaa.gov
https://cdn.star.nesdis.noaa.gov
```

The NOAA catalog supplies available areas, products, image sizes, and
latest image links. The Windows application checks this metadata in the
background at startup when its saved catalogue is older than 24 hours,
including when EUMETSAT is selected. This downloads metadata only. No NOAA
account or API key is required.

Himawari uses the official NICT and JMA public image services:

```text
https://himawari8.nict.go.jp
https://jh190005-4.kudpc.kyoto-u.ac.jp/himawari
https://ds.data.jma.go.jp/mscweb/data/himawari
```

The shared startup catalogue job checks expired Himawari metadata. No Himawari
account or API key is required. Image pixels are downloaded only when Himawari
is selected or an active rotation profile uses it.

CIRA SLIDER uses its public catalogue, latest-time metadata, and PNG tile host:

```text
https://slider.cira.colostate.edu
```

The shared startup catalogue job checks expired SLIDER satellite, sector, product, and
source-size metadata. No CIRA account or API key is required. Product pixels are
downloaded only when CIRA SLIDER is selected or an active rotation profile uses
it. MarbleScape does not request SLIDER's separate map or latitude/longitude
overlays, so its wallpapers contain the clean product imagery.

NASA Worldview imagery is accessed through the public Global Imagery Browse
Services (GIBS) WMTS catalogue, time-domain, and WMS endpoints:

```text
https://gibs.earthdata.nasa.gov/wmts/epsg4326/best
https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi
```

The shared startup job checks expired GIBS capabilities metadata so its current
visualization layers, dates, and render sizes are available in Settings. This
metadata document is several megabytes. A complete catalogue checked within the
previous 24 hours is reused without downloading it again; imagery pixels are requested only when
NASA Worldview is selected or used by an active rotation profile. No NASA
account or API key is required. MarbleScape requests the selected data layer
without Worldview's separate map labels, borders, or coordinate overlays.

Copernicus uses the official identity, STAC Catalog, and Process endpoints:

```text
https://identity.dataspace.copernicus.eu
https://sh.dataspace.copernicus.eu/catalog/v1/search
https://sh.dataspace.copernicus.eu/process/v1
https://gisco-services.ec.europa.eu
```

Copernicus rendering requires a free Sentinel Hub OAuth client created under
`User Settings > OAuth clients` in the
[Copernicus Data Space Sentinel Hub portal](https://shapps.dataspace.copernicus.eu/dashboard/#/account/settings).
MarbleScape uses the CDSE OAuth, Catalog, and Process APIs directly. It does not
use Planet Insights, require a Planet subscription, or require a Configuration
Instance. Configuration Instances are needed for OGC services such as WMS/WCS;
MarbleScape sends the selected visualization as an inline Process API evalscript.
The portal's **Credits** view displays the effective quota assigned to the
account. Quota assignments can differ from the public General Users table. For
example, the account used during development showed 30,000 monthly requests and
30,000 monthly Processing Units in September 2026. These values describe that
account rather than a guaranteed allowance for every user. The Client ID is
stored in the configuration; on Windows the Client secret is encrypted for
the current Windows user with DPAPI. It can instead be supplied through
`MARBLESCAPE_COPERNICUS_CLIENT_ID` and
`MARBLESCAPE_COPERNICUS_CLIENT_SECRET`. GISCO map tiles are requested only
when the Copernicus background or labels require them.

The OAuth client provides authentication only. Its API access, rate limits,
monthly request allowance, and Processing Unit (PU) allowance are inherited
from the associated CDSE user account and account type. Creating another OAuth
client does not provide another quota. For the active account, the values shown
in its Credits view are authoritative. The
[current CDSE quota table](https://documentation.dataspace.copernicus.eu/Quotas.html)
describes the general account categories; service limits and individual
assignments can change.

Catalogue searches and image processing use the account separately. Processing
cost depends on factors including requested pixels, input bands, temporal
samples, and processing options. A wallpaper larger than one Process API request
is assembled from multiple requests, so higher output resolutions generally use
more requests and PUs. Availability in the wider Copernicus Data Space catalogue
also does not guarantee that every mission or product can be processed through
Sentinel Hub. The available Sentinel Hub collections, their processing options,
and the user's permissions determine what MarbleScape can render. Some services,
including Batch Processing V2, are unavailable to Copernicus General Users.
When access is denied, a rate or monthly quota is exhausted, or a collection is
unavailable, MarbleScape reports the source error and keeps the previous
wallpaper instead of replacing it with an incomplete result.

Console output may contain local configuration and output paths. Remove or
redact those paths before publishing diagnostic logs.

The Windows menu item `Start with Windows` stores the local launch command in:

```text
HKCU\Software\Microsoft\Windows\CurrentVersion\Run
```

The command includes the current installation and the active `--config` path,
with Windows path quoting. Temporary flags such as `--once` are excluded.
Settings reports startup enabled only when the registration matches this
launch target and configuration. Registry changes are rolled back if saving
the accompanying configuration fails.
