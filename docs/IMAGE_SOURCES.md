# Image source guide

Provider selection, missions, satellites, layers, projections, cloud cover, and source-specific behavior.

[Back to the main README](../README.md)

## Image sources

### Quick source selection

| Requirement | Recommended selection |
| --- | --- |
| Updates on a minutes-scale | GOES, Himawari, EUMETSAT, or CIRA SLIDER |
| Natural-looking geostationary Earth disk | EUMETSAT MTG GeoColour or CIRA SLIDER GeoColor |
| No separately rendered borders or coordinate grid | CIRA SLIDER |
| High-resolution local imagery | Copernicus Sentinel-2 L2A |
| Radar through clouds or at night | Copernicus Sentinel-1 |
| Global daily environmental imagery | NASA Worldview |
| European weather | EUMETSAT MTG |
| Weather over the Americas | GOES-East or GOES-West |
| Asia-Pacific weather | Himawari |
| Polar regions | EUMETSAT Metop/Sentinel-3 or NASA Worldview |
| A fixed historical date | Copernicus Browser, NASA Worldview, or a compatible EUMETSAT layer |
| The Sun | Solar / Sun SUVI |

### Source and use-case matrix

The following tables describe the sources and selections currently supported by
MarbleScape. Live catalogues remain authoritative when a provider changes its
satellites, sectors, products, or layer names.

| Image source | Satellites or missions | Suitable layers or products | Coverage | Projection in MarbleScape | Best suited to |
| --- | --- | --- | --- | --- | --- |
| **EUMETSAT** | MTG, MSG, Metop, Sentinel-3, and multi-mission products | GeoColour, True Color, infrared, water vapour, Air Mass, Dust, sea-surface temperature, and OLCI products | Europe, Africa, the Atlantic, and Indian Ocean; LEO products also provide global overpass strips | Selectable: Geographic, three GEOS views, Mercator, North Polar, and South Polar | European weather, geostationary Earth disks, marine and atmospheric products, and polar views |
| **NOAA GOES** | GOES-19 (East) or GOES-18 (West), selected in the Satellite dropdown | GeoColor, infrared, water vapour, Air Mass, Dust, fire, and cloud products | East: the Americas and Atlantic; West: western America and the eastern/central Pacific | Fixed NOAA view | Weather over the Americas or Pacific, including hurricanes |
| **Solar / Sun** | GOES SUVI | Fe094, Fe131, Fe171, Fe195, Fe284, and Fe304 | The Sun | Fixed solar view | The solar corona, active regions, and solar events |
| **Himawari** | Himawari-9 | True Color, enhanced True Color, B13 infrared, water vapour, Dust, Ash, Air Mass, and Night Microphysics | East Asia, Australia, and the western Pacific | Native geostationary view | Asia-Pacific weather, typhoons, and volcanic ash |
| **CIRA SLIDER** | GOES-18/19, Himawari-9, GEO-KOMPSAT-2A, Meteosat/MTG, JPSS, and future catalogue entries | GeoColor and the products published for each sector | Depends on the selected satellite and sector | Fixed SLIDER sector projection | Clean product imagery without SLIDER borders, maps, or latitude/longitude lines |
| **NASA Worldview** | VIIRS NOAA-20/21, Suomi NPP, MODIS Terra/Aqua, and other GIBS missions | True Color, aerosols, fire, snow and ice, sea-surface temperature, vegetation, and atmospheric products | Global | Geographic, EPSG:4326 | Global daily imagery and thematic environmental observation |
| **Copernicus Browser** | Sentinel-1/2/3/5P, Copernicus DEM, and Landsat 8/9 | Radar, True Color, NDVI, SWIR, Moisture, atmospheric gases, sea-surface temperature, and terrain | Local or regional satellite overpasses | Web Mercator, EPSG:3857, framed with latitude, longitude, and map zoom | High-resolution land observation, radar, vegetation, terrain, and atmospheric products |

### Recommended source and layer by purpose

| Purpose | Recommended source | Mission or satellite | Recommended layer or product |
| --- | --- | --- | --- |
| Current natural-looking Earth view over Europe and Africa | EUMETSAT | MTG | **GeoColour** |
| Europe without separate border overlays | CIRA SLIDER | Meteosat-12 or MTG | **GeoColor**, Full Disk |
| Americas and Atlantic | NOAA GOES → GOES-East | GOES-19 | **GeoColor** |
| Western America, Pacific, or Hawaii | NOAA GOES → GOES-West | GOES-18 | **GeoColor** |
| East Asia, Australia, or western Pacific | Himawari | Himawari-9 | **NICT True Color Full Disk** |
| Clouds at night | GOES, Himawari, or EUMETSAT | A suitable geostationary satellite | **Infrared**, **B13**, or **Clean Longwave IR** |
| Water vapour and upper-level flow | GOES, Himawari, or EUMETSAT | A suitable geostationary satellite | **Water Vapor** or **Water Vapour** |
| Jet-stream structure and dry stratospheric air | GOES, Himawari, or EUMETSAT | A suitable geostationary satellite | **Air Mass RGB** |
| Fog and low cloud at night | Himawari or EUMETSAT | Himawari, MTG, or MSG | **Night Microphysics RGB** |
| Desert dust over land and sea | Himawari, GOES, or EUMETSAT | A suitable geostationary satellite | **Dust RGB** |
| Volcanic ash | Himawari or EUMETSAT | Himawari, MTG, or MSG | **Ash RGB** |
| Developing thunderstorms | GOES or Himawari | A suitable geostationary satellite | **Sandwich** or **Day Convective Storm RGB** |
| Current wildfires and thermal anomalies | GOES or NASA Worldview | GOES, VIIRS, or MODIS | A fire, hotspot, or short-wave infrared product |
| High-resolution land surface | Copernicus Browser | Sentinel-2 | **Sentinel-2 L2A · True color** |
| Local vegetation condition | Copernicus Browser | Sentinel-2 | **NDVI** |
| Soil and vegetation moisture | Copernicus Browser | Sentinel-2 | **Moisture index** or **SWIR** |
| Burn scars or smoke-penetrating views | Copernicus Browser | Sentinel-2 or Landsat | **Wildfires** or **SWIR** |
| Urban and built-up structures | Copernicus Browser | Sentinel-2 or Sentinel-1 | **False color (urban)** or **SAR urban** |
| Observation through cloud or darkness | Copernicus Browser | Sentinel-1 | **IW VV+VH · Enhanced visualization** or **RGB ratio** |
| Flood mapping | Copernicus Browser | Sentinel-1 IW | A **VV/VH Enhanced visualization** |
| Sea-surface temperature | EUMETSAT or Copernicus Browser | Sentinel-3 SLSTR | A sea-surface-temperature or SLSTR L2 layer |
| Ocean colour, algae, or suspended material | EUMETSAT or Copernicus Browser | Sentinel-3 OLCI | A chlorophyll, algal-pigment, or suspended-matter layer |
| Air pollution and atmospheric composition | Copernicus Browser | Sentinel-5P | **NO2**, **SO2**, **CO**, **CH4**, **O3**, or an aerosol layer |
| Terrain without current satellite imagery | Copernicus Browser | Copernicus DEM | **Topographic**, **Color**, or **Grayscale** |
| Global environmental overview | NASA Worldview | VIIRS, MODIS, or another GIBS mission | The matching thematic GIBS layer |
| Solar observation | Solar / Sun | GOES SUVI | **Fe171** as a general-purpose default |

Product names can differ slightly between providers. NOAA products may already
contain borders or grids in their published image pixels. Select CIRA SLIDER
when a comparable clean product without SLIDER's optional overlays is required.

### Copernicus mission guide

| Mission | Observation type | Strengths | Main limitations |
| --- | --- | --- | --- |
| **Sentinel-1** | Synthetic-aperture radar | Day and night operation, largely independent of weather, useful for water and surface structure | Does not produce a natural-colour photograph |
| **Sentinel-2 L2A** | Atmospherically corrected optical imagery | True Color, vegetation, moisture, fires, and detailed land observation | Clouds and local overpass strips; wide low-zoom views can contain large no-data areas |
| **Sentinel-2 L1C** | Top-of-atmosphere optical imagery | Less-processed optical measurements | L2A is normally the better wallpaper and land-analysis starting point |
| **Sentinel-3 OLCI** | Medium-resolution optical imagery | Ocean colour, vegetation, and wider areas | Less spatial detail than Sentinel-2 |
| **Sentinel-3 SLSTR** | Thermal and optical imagery | Land- and sea-surface temperature | Primarily thematic imagery rather than a natural photograph |
| **Sentinel-5P** | Atmospheric spectrometry | Trace gases, aerosols, and air-quality products | Coarse spatial resolution |
| **Copernicus DEM** | Digital elevation model | Terrain and relief with broad coverage | Timeless terrain data rather than a current satellite image |
| **Landsat 8/9** | Optical and thermal imagery | Land and water analysis and long historical time series | Longer revisit intervals than geostationary weather sources |

New installations start with **Sentinel-2 Mosaics · Sentinel-2 Quarterly Mosaics ·
True Color Cloudless**. Select **Sentinel-2 L2A · True color** for a recent
individual acquisition or finer local detail.
Map zoom describes the requested geographic extent, not the optical zoom of a
continuous global photograph. At a small map zoom, a Sentinel-2 overpass can
occupy only a tiny part of the output. This is especially noticeable with
**Fill areas without image data with black**.

### EUMETSAT projection guide

| Projection | Best suited to | Recommended missions | Notes |
| --- | --- | --- | --- |
| **Geographic - EPSG:4326** | Rectangular world and regional views | All compatible layers | The simplest choice for Europe and custom longitude/latitude extents; polar regions are distorted |
| **GEOS: MSG FES, MTG FD** | A round full-disk view centred at 0 degrees | MTG and MSG Full Earth Scan | The most natural Earth-disk view for Europe and Africa |
| **GEOS: MSG RSS** | Rapid-scan views of Europe | MSG Rapid Scanning Service | Intended for the rapid-scan sector rather than a global view |
| **GEOS: MSG IODC** | The Indian Ocean region | MSG IODC | Less suitable for a Europe-centred wallpaper |
| **Spherical Mercator - EPSG:3857** | Familiar web-map and regional views | Reprojectable layers | The poles are cropped and strongly distorted |
| **North Polar - EPSG:3995** | The Arctic | Metop, Sentinel-3, and other polar orbiters | MTG and MSG do not observe the pole |
| **South Polar - EPSG:3976** | Antarctica | Metop, Sentinel-3, and other polar orbiters | Geostationary imagery has large coverage gaps there |

The Projection control applies only to EUMETSAT. NOAA, Himawari, CIRA SLIDER,
NASA Worldview, and Copernicus use their provider-native or internally defined
projection. Reprojection changes presentation but cannot create imagery outside
a satellite's observed area.

### NOAA GOES and Solar imagery

The [official NOAA STAR GOES Image Viewer](https://www.star.nesdis.noaa.gov/goes/index.php)
provides GOES-East and GOES-West full disks, continental views, regional
sectors, Weather Forecast Office areas, mesoscale sectors, and available
storm views. Area and product availability follows the published catalog;
mesoscale and storm views can change over time. Solar/Sun is a separate
SUVI source, with wavelength products provided by NOAA.

Only JPEG and PNG still images are used. Some large NOAA products are
offered as a ZIP containing a still image; these are decoded as images.
GIF animations and videos are excluded. Size choices come from the
selected product instead of an assumed common size list.

New selections default to **Automatic (recommended)**, which chooses the
smallest available source resolution suitable for the configured output and
zoom. **Largest available** and explicitly saved sizes remain available. A separate WMS render
quality factor is not used for NOAA: Source resolution determines available
detail, and the image is resized with Lanczos filtering to the output dimensions.
**Filter areas** narrows the list within the chosen
category; it does not change the selected location. Selecting Active storms
chooses the first listed storm area. Apply saves that active selection.

**Refresh all catalogues**, to the left of Refresh catalogue, refreshes all
NOAA, Himawari, CIRA SLIDER, NASA Worldview, and EUMETSAT metadata with progress and error reporting.
It can take a while. At application startup, MarbleScape reuses complete NOAA,
Himawari, CIRA SLIDER, NASA Worldview, and EUMETSAT catalogues for up to 24
hours after each source's last successful online check. Expired sources are
checked separately. If a source is unavailable, its complete saved catalogue
remains usable and another automatic online attempt is postponed for one hour.
A manual refresh can retry immediately.
**Refresh catalogue** and **Refresh all catalogues** always request an online
check immediately.
On source selection, saved catalogue entries appear immediately when
available; use **Refresh catalogue** to request a new online check. The last
successful metadata is also stored in
`content/catalogues.json`. If a provider is unavailable or returns an
incomplete catalogue, MarbleScape reports the problem and uses this disk cache
when it contains the requested source. Successful remote results are compared
with the local values first; unchanged catalogue entries are kept while each
source's check time is recorded.
After a NOAA failure, cached GOES-East, GOES-West, and Solar selections remain
editable. Automatic source resolution is the default for both GOES satellites.
Within a running NOAA client, area metadata has a five-minute memory lifetime
and product lists have a one-day memory lifetime. The persisted 24-hour startup
window is separate from these in-memory limits. Himawari metadata retains its
provider-level memory cache between explicit refreshes.
When Copernicus OAuth credentials are configured, startup also refreshes and
caches the acquisition dates for the saved Copernicus location and selection.
Selecting Copernicus refreshes that location-specific date list again. Its
bundled Browser mission, product, and layer catalogue is already available
locally without a network refresh.
An animated progress bar remains active during catalogue loading, including
individual source refreshes. Successful completion shows **Completed.**;
an incomplete refresh shows **Finished with issues.** followed by a separator
and the provider messages. Cached selections remain available where possible.
Each image update still
checks the latest published image independently of the catalog cache.

Some STAR products already contain boundaries, coastlines or grid lines in
their JPEG pixels. MarbleScape cannot switch those embedded lines off.
EUMETSAT supports separate optional overlay layers in the configuration. Select
CIRA SLIDER when clean source tiles without its separate border or coordinate
overlays are preferred.

NOAA selections use the latest published image available at each update
check. Source publication delays and outages can prevent a newer image
from being available. A failed download keeps the last valid wallpaper;
it does not make an older image a new observation. The update interval
sets how often MarbleScape checks, not how often NOAA produces imagery.
Large source sizes take more download time and memory; increasing the
wallpaper output size cannot add detail absent from the source.

NOAA can occasionally publish a valid but almost entirely black JPEG for one
resolution while another size contains normal imagery. The listed size remains
selectable, so it can be used again when NOAA restores its content. If the
original image on NOAA is black, select a smaller source resolution temporarily.

### Himawari imagery

The [official NICT Himawari viewer](https://himawari8.nict.go.jp/) supplies
timestamped PNG tiles for True Color Full Disk, True Color Japan, and all 16 AHI
bands. Full Disk True Color offers source sizes from 550 × 550 through
11000 × 11000, Japan through 3000 × 2400, and the band view through
5500 × 5500. AHI bands are composited over the same Blue Marble base used by
the NICT viewer. The NICT coastline overlay is a separate viewer control and is
not added to these source pixels.

The [official JMA Himawari real-time catalogue](https://ds.data.jma.go.jp/mscweb/data/himawari/index.html)
supplies Full Disk, Australia, New Zealand, Japan, Central/South/Southeast Asia,
Pacific Islands, high-resolution regional, heavy-rainfall, high-resolution
heavy-rainfall views for ten Pacific island locations, and target-area still
images. MarbleScape lists the products published for each JMA view and
validates the downloaded JPEG against that view's native dimensions. JMA
annotations already present in a published JPEG remain part of the image.

Every update resolves the provider's newest timestamp first. Only PNG and JPEG
stills are accepted; the animation and movie controls on the provider sites are
excluded. **Largest available** remains dynamic. For NICT Full Disk it currently
means a 20 × 20 grid (400 source tiles), so the highest setting can take
noticeably longer and use more network traffic. Tiles are resized directly into
the wallpaper canvas to avoid allocating an 11000 × 11000 intermediate image.
Fit mode, zoom, background color, and output size work like the NOAA sources.

**Refresh catalogue** refreshes Himawari metadata for the selected source.
**Refresh all catalogues** refreshes NOAA, Himawari, CIRA SLIDER, NASA Worldview,
and EUMETSAT metadata together; it
does not download wallpaper images or query the location-specific Copernicus
acquisition list.

### CIRA SLIDER imagery

The [CIRA SLIDER viewer](https://slider.cira.colostate.edu/) provides a live
catalogue of satellites, sectors, products, product-specific tile-pyramid
levels, and latest acquisition times. MarbleScape exposes those dependent
choices as **Satellite**, **Sector**, **Product / layer**, and **Source
resolution**. This includes the currently published GOES-East, GOES-West,
Himawari-9, GEO-KOMPSAT-2A, Meteosat, MTG, and JPSS entries and follows future
catalogue changes without hard-coding the visible list.

Only the newest timestamped PNG still is used. Animations and archived playback
are excluded. Each wallpaper is assembled directly from the selected product's
PNG tiles and then processed with the common fit/crop, zoom, background, and
output-size settings. SLIDER's **Default Borders**, other maps, and **Lat/Lon**
grid are separate viewer layers. MarbleScape intentionally does not fetch them,
so both borders and coordinate lines are off for this source.

The highest available level can contain hundreds of tiles. MarbleScape caps a
full tile grid at 1,024 responses and renders tiles directly into the output
canvas to limit memory use. A high source resolution therefore retains detail
but can take considerably longer and transfer more data. Selecting a smaller
source resolution reduces the request count. **Refresh catalogue** reloads the
live SLIDER definition; the bundled fallback retains core full-disk GeoColor
choices when the catalogue endpoint is temporarily unavailable.

### NASA Worldview imagery

[NASA Worldview](https://worldview.earthdata.nasa.gov/) is powered by
[NASA GIBS](https://nasa-gibs.github.io/gibs-api-docs/access-basics/).
MarbleScape reads the official EPSG:4326 `best` WMTS capabilities document and
lists every still-image visualization with usable geographic tile-matrix
metadata. The dependent controls are **Layer category**, **Imagery layer**,
**Date / time**, and **Render resolution**. **Filter layers** searches the
current category by title or GIBS layer ID.

Time-dependent layers default to **Latest available**. Before an image check,
MarbleScape queries the layer's GIBS time domain and uses its newest available
acquisition; it falls back to the capabilities default if the small time-domain
request is temporarily unavailable. The date dropdown also exposes up to 100
recent fixed values described by the layer metadata. Layers without a time
dimension are marked **Timeless**.

The selected layer is requested as one PNG from the GIBS WMS 1.3 service over
the full geographic world extent. MarbleScape does not scrape the interactive
Worldview application, request animations, or download Worldview's separate
map labels, borders, and latitude/longitude overlays. An annotation embedded in
a provider visualization remains part of that layer's pixels.

Render sizes are derived from the layer's published tile-matrix levels and are
limited to safely processable choices up to 8,192 pixels on one axis and about
33.5 million source pixels. **Largest available** is resolved from the current
catalogue each time. The wallpaper Output size remains independent; fit/crop,
zoom, and background color are applied locally with Lanczos resampling. If a
large WMS response times out or is rejected upstream, select a smaller Render
resolution. A failed request keeps the previous wallpaper.

**Refresh catalogue** reloads the NASA GIBS capabilities for this source.
The startup job checks this metadata when its persisted 24-hour cache has
expired; **Refresh all catalogues** checks it immediately. Neither job downloads
imagery pixels. The capabilities catalogue is also cached in memory for one hour;
the newest time for the selected layer is checked independently during normal
image checks. A small bundled true-color fallback keeps a saved
selection visible during a catalogue outage, but a live GIBS connection is
still required to download its image.

### Copernicus Browser imagery

Copernicus selections use dependent dropdowns for mission/dataset,
configuration, product, visualization layer, Browser highlight, and acquisition
date. The configuration, product, layer, and highlight choices come from a
bundled snapshot of the official Copernicus Browser catalogue. **Refresh
Copernicus catalogue** queries the live STAC Catalog API for every acquisition
date available at the current latitude/longitude and selection. **Latest
available** is the default and is resolved again before each image download.
The initial Browser selection is Sentinel-2 Mosaics with Sentinel-2 Quarterly
Mosaics and True Color Cloudless. Saved user selections remain unchanged.
The **Mosaic brightness** slider applies only to Sentinel-1 and Sentinel-2
mosaic layers. Its 100% setting uses the normal rendering; 25-200% in 5% steps
adjusts the selected profile without changing other image sources. Sentinel-2
cloudless mosaics use the Browser's optical contrast curve. Sentinel-1 IW RGB
Ratio has a separate lower default gain. Sentinel-1 DH monthly mosaics mainly
cover polar regions; if a selected region and month have no valid imagery,
MarbleScape reports that rather than presenting the map background as imagery.
For optical layers that support it, **Maximum cloud cover** filters satellite
tiles by their published cloud-cover estimate. The slider runs from 0% to 100%
in 5% steps and defaults to 30%, matching the Browser's initial Sentinel-2
selection. It is an inclusive upper limit: 20% accepts tiles estimated at 20%
or less, while 0% requires an estimate of exactly 0% and may return no image.
The slider also applies to **Single latest acquisition**. 100% allows every
cloud-cover value. The estimate applies to whole satellite tiles, so the visible
map can contain more clouds than the selected percentage. The control is
disabled for layers without this metadata, including Sentinel-1 radar and DEM.

Increasing the limit admits more cloudy tiles. This *may* give the latest
qualifying date better coverage of the requested view; with gap filling it can
reduce the need to use older dates, giving a more temporally consistent picture.
A lower limit excludes more tiles and may leave gaps or, with gap filling,
produce a patchwork of acquisitions from different dates. It does **not**
directly reduce or increase the physical number of satellite tiles needed for
the map. Neither setting guarantees a seamless result: changing the limit can
also change which date counts as the latest qualifying acquisition, and the
tile-wide estimate does not measure clouds in the exact map view. With
**Single latest acquisition**, missing areas are not filled from earlier dates;
there is no older-date patchwork in that mode. See the [Sentinel-2 L2A filtering
and mosaicking documentation](https://docs.sentinel-hub.com/api/latest/data/sentinel-2-l2a/).

Sentinel-1 is the radar mission. Sentinel-2, Sentinel-3, Sentinel-5P,
Copernicus DEM, and Landsat are included because the Copernicus Browser also
offers them as visual products. Enter a custom latitude and longitude and use
Map zoom to frame the output. Browser highlights load their saved product,
layer, position, zoom, and acquisition date and can then be edited.
The zoom dropdown follows the official limits of the selected dataset: 7-18
for Sentinel-1 and Sentinel-2 L2A, 10-18 for Sentinel-2 L1C, 5/6-18 for
Sentinel-3 products, 3-19 for Sentinel-5P, 7-18 for Landsat, and 7-25 for DEM.
The provider currently restricts COPERNICUS_30 DEM access to authorized CCM
users; COPERNICUS_90 remains the unrestricted DEM default on the service.

The Process API renders the selected official evalscript. **Coverage mode**
offers a single latest acquisition, black no-data areas, or a gap-filling
composite. Gap filling uses the most recent valid pixel from the selected 3,
7, 14, 21, 30, 45, 60, 90, 120, 180, 270, 365, 550, 730, 920, or 1095-day
lookback; 14 days is the default for new profiles.
The month and year labels in the dropdown are approximate; the day count
controls the actual range. Any area
still without imagery uses the map background. Outputs above the
Process API's 2500 × 2500 pixel request limit are split into tiles and joined
without reducing the configured wallpaper resolution. In gap-fill mode, a large
response with transparent areas is checked again in 512-pixel spatial tiles.
Only tiles containing missing pixels are requested again; existing image pixels
are kept. This can recover coverage that the Process API omits from a broad
request, but it adds API requests and cannot create imagery where none exists.

**Map labels** adds the GISCO/OpenStreetMap place, road, POI, and boundary
overlay. The black coverage mode keeps transparent no-data pixels black.
Attribution is written into
generated images whenever map tiles are used.
The cloud limit is applied to both acquisition-date discovery and image
rendering. With **Single latest acquisition** or black no-data mode, MarbleScape
uses the latest qualifying date and does not fill uncovered areas from earlier
dates. With gap filling, every contributing tile must meet the cloud limit and
the selected lookback still bounds how far back older imagery may be used.
The lookback applies to gap filling relative to the chosen acquisition date;
**Latest available** searches for the newest qualifying acquisition separately.
Fixed dates stay fixed; a stricter cloud limit may leave part or all of that
date without imagery.
Temporary image-transfer failures and retryable service responses use the
global **Download retries** setting. Copernicus and GISCO HTTPS connections use a current Mozilla CA
bundle in addition to the operating-system certificate store. If the optional
GISCO map background or label service remains unavailable, MarbleScape keeps
the successfully rendered satellite imagery and uses black for uncovered
pixels; the map overlays are omitted and the reason is logged. A Copernicus
image or catalogue failure keeps the previous wallpaper and reports the source
as unavailable. Invalid requests are reported immediately with the service
detail so their settings can be corrected.
