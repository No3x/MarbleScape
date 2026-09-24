"""Offline contract tests for the Copernicus Catalog and Process provider."""

import base64
import datetime as dt
import io
import json
import time
import tomllib
import unittest
import urllib.error
from unittest import mock

from PIL import Image

import marblescape_copernicus as copernicus
from marblescape_copernicus_mosaics import evalscript_for_brightness
import marblescape_download as app


L2A_PROFILE = {
    **copernicus.DEFAULT_PROFILE,
    "mission": "Sentinel-2",
    "product": "DEFAULT-THEME::a91f72",
    "layer": "1_TRUE_COLOR",
    "coverage_mode": "fill_gaps",
}


class _Response:
    def __init__(self, data, content_type):
        self._stream = io.BytesIO(data)
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, maximum=-1):
        return self._stream.read(maximum)


def _png(size, color=(10, 20, 30, 255)):
    output = io.BytesIO()
    Image.new("RGBA", size, color).save(output, format="PNG")
    return output.getvalue()


class CatalogueTests(unittest.TestCase):
    def test_cdse_management_and_api_endpoints_are_kept_separate(self):
        self.assertEqual(
            copernicus.ACCOUNT_SETTINGS_URL,
            "https://shapps.dataspace.copernicus.eu/dashboard/#/account/settings",
        )
        self.assertEqual(copernicus.PROCESS_URL, "https://sh.dataspace.copernicus.eu/process/v1")
        self.assertEqual(copernicus.CATALOG_URL, "https://sh.dataspace.copernicus.eu/catalog/v1/search")
        self.assertIn("identity.dataspace.copernicus.eu", copernicus.TOKEN_URL)

    def test_bundled_browser_catalogue_is_complete_and_process_compatible(self):
        catalogue = copernicus.load_catalogue()
        products = [product for theme in catalogue["themes"] for product in theme["products"]]
        layers = [layer for product in products for layer in product["layers"]]
        highlights = [item for theme in catalogue["themes"] for item in theme.get("highlights", [])]
        self.assertEqual(catalogue["source_revision"],
                         "1a1724c42b04e8a0953a410016676daea8ee5d33")
        self.assertEqual((len(catalogue["themes"]), len(products), len(layers), len(highlights)),
                         (13, 60, 367, 103))
        self.assertEqual(set(copernicus.missions()), {
            "Sentinel-1", "Sentinel-1 Mosaics", "Sentinel-2", "Sentinel-2 Mosaics",
            "Sentinel-3", "Sentinel-5P",
            "Copernicus DEM", "Landsat 8/9",
        })
        self.assertEqual({layer["data_type"] for layer in layers}, {
            "dem", "landsat-ot-l1", "sentinel-1-grd", "sentinel-2-l1c",
            "sentinel-2-l2a", "sentinel-3-olci", "sentinel-3-olci-l2",
            "sentinel-3-slstr", "sentinel-3-slstr-l2",
            "sentinel-3-synergy-l2", "sentinel-5p-l2",
            "byoc-5460de54-082e-473a-b6ea-d5cbe3c17cca",
            "byoc-65d4af89-5ce5-468e-bbbe-8a2fd9efaccc",
            "byoc-cc676fec-cb8d-4bc1-adce-1d9658da950b",
            "byoc-3c662330-108b-4378-8899-525fd5a225cb",
        })
        self.assertTrue(all("//VERSION=3" in layer["evalscript"].replace(" ", "")
                            and "dataMask" in layer["evalscript"] for layer in layers))
        for layer in layers:
            self.assertLessEqual(set(layer.get("data_filter", {})), {
                "mosaickingOrder", "maxCloudCoverage", "acquisitionMode",
                "polarization", "resolution", "orbitDirection", "timeliness",
                "view", "demInstance",
            })
            self.assertLessEqual(set(layer.get("processing", {})), {
                "upsampling", "downsampling", "orthorectify", "demInstance",
                "backCoeff", "speckleFilter", "clampNegative", "egm",
            })
            if layer["data_type"] == "sentinel-1-grd":
                self.assertNotIn("demInstance", layer.get("data_filter", {}))
                self.assertIn("demInstance", layer.get("processing", {}))
            if layer["data_type"] == "sentinel-3-slstr":
                self.assertEqual(layer.get("data_filter", {}).get("view"), "NADIR")
                self.assertNotIn("view", layer.get("processing", {}))
        for product in products:
            for layer in product["layers"]:
                if layer["data_type"] == "dem":
                    expected = ("COPERNICUS_30" if "COPERNICUS_30" in product["name"]
                                else "COPERNICUS_90")
                    self.assertEqual(layer.get("data_filter", {}).get("demInstance"), expected)

    def test_profile_normalization_validates_catalogue_location_date_and_flags(self):
        value = copernicus.normalize_profile({})
        self.assertEqual(value, copernicus.DEFAULT_PROFILE)
        self.assertIsNot(value, copernicus.DEFAULT_PROFILE)
        value = copernicus.normalize_profile({**copernicus.DEFAULT_PROFILE,
                                              "date": "2026-09-01",
                                              "latitude": 52, "longitude": 13})
        self.assertEqual(value["date"], "2026-09-01")
        self.assertEqual(value["latitude"], 52.0)
        self.assertEqual(value["coverage_mode"], "single")
        self.assertEqual(value["lookback_days"], 14)
        self.assertEqual(value["max_cloud_cover"], 30)
        self.assertEqual(value["brightness"], 100)
        self.assertEqual(copernicus.LOOKBACK_DAYS,
                         (3, 7, 14, 21, 30, 45, 60, 90, 120, 180, 270, 365, 550, 730, 920, 1095))
        for days in copernicus.LOOKBACK_DAYS:
            with self.subTest(lookback_days=days):
                normalized = copernicus.normalize_profile({
                    **copernicus.DEFAULT_PROFILE, "lookback_days": days,
                })
                self.assertEqual(normalized["lookback_days"], days)
        for update in (
            {"mission": "Sentinel-1"}, {"product": "missing"}, {"layer": "missing"},
            {"date": "today"}, {"latitude": 90}, {"longitude": 181},
            {"map_zoom": 7.0}, {"map_labels": 1},
            {"coverage_mode": "missing"}, {"lookback_days": 5}, {"lookback_days": True},
            {"max_cloud_cover": -5}, {"max_cloud_cover": 105},
            {"max_cloud_cover": 33}, {"max_cloud_cover": True},
            {"brightness": 24}, {"brightness": 205},
            {"brightness": 33}, {"brightness": True},
        ):
            with self.subTest(update=update), self.assertRaises(ValueError):
                copernicus.normalize_profile({**copernicus.DEFAULT_PROFILE, **update})

    def test_default_uses_quarterly_true_color_mosaic_and_preserves_saved_l2a(self):
        product = copernicus.get_product(
            copernicus.DEFAULT_PROFILE["configuration"], copernicus.DEFAULT_PROFILE["product"]
        )
        layer = copernicus.get_layer(product, copernicus.DEFAULT_PROFILE["layer"])
        self.assertEqual(copernicus.DEFAULT_PROFILE["mission"], "Sentinel-2 Mosaics")
        self.assertEqual(product["name"], "Sentinel-2 Quarterly Mosaics")
        self.assertEqual(layer["name"], "True Color Cloudless")
        self.assertEqual(copernicus.map_zooms(layer)[0], 2)
        self.assertEqual(copernicus.normalize_profile(L2A_PROFILE), L2A_PROFILE)

    def test_sentinel_2_l2a_zoom_ranges(self):
        product = copernicus.get_product("DEFAULT-THEME", L2A_PROFILE["product"])
        layer = copernicus.get_layer(product, L2A_PROFILE["layer"])
        self.assertEqual(product["name"], "Sentinel-2 L2A")
        self.assertEqual(layer["data_type"], "sentinel-2-l2a")
        expected = {
            "sentinel-1-grd": (7, 18),
            "sentinel-2-l1c": (10, 18),
            "sentinel-2-l2a": (7, 18),
            "sentinel-3-olci": (6, 18),
            "sentinel-3-olci-l2": (6, 18),
            "sentinel-3-slstr": (6, 18),
            "sentinel-3-slstr-l2": (5, 18),
            "sentinel-3-synergy-l2": (6, 18),
            "sentinel-5p-l2": (3, 19),
            "landsat-ot-l1": (7, 18),
            "dem": (7, 25),
        }
        layers = [item for theme in copernicus.themes() for product in theme["products"]
                  for item in product["layers"]]
        for data_type, bounds in expected.items():
            with self.subTest(data_type=data_type):
                matching = next(item for item in layers if item["data_type"] == data_type)
                zooms = copernicus.map_zooms(matching)
                self.assertEqual((zooms[0], zooms[-1]), bounds)

        self.assertEqual(
            copernicus.map_zooms_for_view(layer, 51.1657, (14400, 8640)),
            tuple(range(7, 19)),
        )
        s5p_layer = next(item for item in layers if item["data_type"] == "sentinel-5p-l2")
        self.assertEqual(
            copernicus.map_zooms_for_view(s5p_layer, 51.1657, (3840, 2160))[0], 4
        )

        l1c_product = next(
            product for product in copernicus.products("DEFAULT-THEME", "Sentinel-2")
            if product["name"] == "Sentinel-2 L1C"
        )
        with self.assertRaisesRegex(ValueError, "10 through 18"):
            copernicus.normalize_profile({
                **L2A_PROFILE,
                "product": l1c_product["id"],
                "layer": l1c_product["layers"][0]["id"],
                "map_zoom": 7,
            })

    def test_mosaics_use_their_own_collections_and_low_zoom_geometry(self):
        quarterly = copernicus.get_product("DEFAULT-THEME", "MARBLESCAPE::S2-QUARTERLY")
        annual = copernicus.get_product("DEFAULT-THEME", "MARBLESCAPE::S2-WORLDCOVER-ANNUAL")
        monthly = copernicus.get_product("DEFAULT-THEME", "MARBLESCAPE::S1-IW-MONTHLY")
        self.assertEqual(
            tuple(product["missions"][0] for product in (quarterly, annual, monthly)),
            ("Sentinel-2 Mosaics", "Sentinel-2 Mosaics", "Sentinel-1 Mosaics"),
        )
        self.assertEqual(copernicus.map_zooms(quarterly["layers"][0])[0], 2)
        self.assertEqual(copernicus.map_zooms(annual["layers"][0])[0], 9)
        self.assertFalse(copernicus.supports_cloud_filter(quarterly["layers"][0]))
        self.assertFalse(copernicus.supports_cloud_filter(annual["layers"][0]))
        self.assertFalse(copernicus.supports_cloud_filter(monthly["layers"][0]))
        for product, mission in ((quarterly, "Sentinel-2 Mosaics"),
                                 (monthly, "Sentinel-1 Mosaics")):
            with self.subTest(product=product["name"]):
                profile = copernicus.normalize_profile({
                    **copernicus.DEFAULT_PROFILE, "mission": mission,
                    "product": product["id"], "layer": product["layers"][0]["id"],
                    "map_zoom": 2,
                })
                self.assertIn(2, copernicus.map_zooms_for_view(
                    product["layers"][0], profile["latitude"], (3840, 2160)
                ))
                bbox = copernicus.geographic_bbox(profile, 3840, 2160)
                self.assertLessEqual(bbox[0], bbox[2])
                self.assertLessEqual(bbox[1], bbox[3])
                client = copernicus.CopernicusClient()
                frame = {"profile": profile, "width": 3840, "height": 2160}
                parts = list(client._process_parts(frame))
                self.assertTrue(parts)
                self.assertTrue(all(part[2] <= copernicus.PROCESS_TILE_LIMIT
                                    and part[3] <= copernicus.PROCESS_TILE_LIMIT
                                    for part in parts))
                _cx, _cy, world_size = copernicus._scaled_view_center(profile, 3840, 2160)
                self.assertEqual(
                    copernicus._collection_for_resolution(
                        product["layers"][0],
                        2 * copernicus.WEB_MERCATOR_HALF_WORLD / world_size,
                    ), product["layers"][0]["low_resolution_data_type"]
                )
        self.assertEqual(
            copernicus._collection_for_resolution(annual["layers"][0], 10000),
            annual["layers"][0]["data_type"],
        )

    def test_mosaic_brightness_changes_only_mosaic_evalscript(self):
        quarterly = copernicus.get_product("DEFAULT-THEME", "MARBLESCAPE::S2-QUARTERLY")
        mosaic = quarterly["layers"][0]
        original = mosaic["evalscript"]
        self.assertIn("var brightness = 1.0;", original)
        self.assertIn("var brightness = 1.50;", evalscript_for_brightness(mosaic, 150))
        self.assertEqual(original, mosaic["evalscript"])
        ordinary = copernicus.get_layer(
            copernicus.get_product("DEFAULT-THEME", L2A_PROFILE["product"]),
            L2A_PROFILE["layer"],
        )
        self.assertEqual(evalscript_for_brightness(ordinary, 150), ordinary["evalscript"])

    def test_account_usage_parses_dashboard_monthly_values_and_role(self):
        claims = {"realm_access": {"roles": ["offline_access", "copernicus-general-quota"]}}
        encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        token = "header." + encoded + ".signature"
        body = {key: {"configuration": "30000", "consumed": "123", "remaining": "29877"}
                for key in ("processingUnitsMonthly", "requestsMonthly")}
        client = copernicus.CopernicusClient("id", "secret")
        with mock.patch.object(client, "access_token", return_value=token), \
                mock.patch.object(client, "_open", return_value=(json.dumps(body).encode(),
                                                                  "application/json")) as opened:
            value = client.account_usage()
        self.assertEqual(value["role"], "copernicus-general-quota")
        self.assertEqual(value["processingUnitsMonthly"], body["processingUnitsMonthly"])
        self.assertEqual(value["requestsMonthly"], body["requestsMonthly"])
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, copernicus.ACCOUNT_USAGE_URL)
        self.assertNotIn("client_secret", request.full_url)

    def test_catalogue_date_parser_accepts_distinct_strings_and_stac_features(self):
        result = {"features": [
            "2026-09-12",
            {"properties": {"datetime": "2026-09-11T10:20:30.123Z"}},
            {"properties": {"date": "2026-09-10"}},
            None,
        ]}
        values = list(copernicus._catalogue_dates(result))
        self.assertEqual([value.date().isoformat() for value in values],
                         ["2026-09-12", "2026-09-11", "2026-09-10"])
        self.assertTrue(all(value.tzinfo == dt.timezone.utc for value in values))

    def test_catalogue_search_uses_location_collection_and_supported_filters(self):
        client = copernicus.CopernicusClient("id", "secret")
        theme = copernicus.get_theme("DEFAULT-THEME")
        product = next(item for item in theme["products"] if "Sentinel-1" in item["missions"])
        layer = product["layers"][0]
        profile = dict(copernicus.DEFAULT_PROFILE, mission="Sentinel-1",
                       product=product["id"], layer=layer["id"])
        payload = client._catalog_payload(profile, product, layer, 1920, 1080,
                                          "2026-09-01T00:00:00Z",
                                          "2026-09-12T00:00:00Z")
        self.assertEqual(payload["intersects"], {"type": "Point", "coordinates": [10.4515, 51.1657]})
        self.assertEqual(payload["collections"], ["sentinel-1-grd"])
        self.assertIn("sar:instrument_mode", payload["filter"])
        self.assertIn("s1:polarization", payload["filter"])

    def test_vector_tile_geometry_decoder_handles_line_features(self):
        def varint(value):
            result = bytearray()
            while value > 0x7F:
                result.append((value & 0x7F) | 0x80)
                value >>= 7
            result.append(value)
            return bytes(result)

        def field(number, wire, value):
            tag = varint((number << 3) | wire)
            return tag + (varint(len(value)) + value if wire == 2 else varint(value))

        # MoveTo (10, 20), then LineTo (15, 17); deltas use zig-zag coding.
        geometry = b"".join(varint(value) for value in (9, 20, 40, 10, 10, 5))
        feature = field(3, 0, 2) + field(4, 2, geometry)
        layer = field(2, 2, feature) + field(5, 0, 4096)
        tile = field(3, 2, layer)
        self.assertEqual(copernicus._decode_vector_tile_lines(tile),
                         [(4096, [(10, 20), (15, 17)])])
        with self.assertRaises(ValueError):
            copernicus._decode_vector_tile_lines(field(3, 2, field(2, 2, feature[:-1])))


class ClientTests(unittest.TestCase):
    def test_transient_network_failures_are_retried_three_times(self):
        attempts = [
            urllib.error.URLError(ConnectionResetError(10054, "reset")),
            urllib.error.URLError(TimeoutError("timed out")),
            _Response(b"ok", "text/plain"),
        ]

        def opener(_request, timeout):
            self.assertEqual(timeout, 17)
            result = attempts.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        client = copernicus.CopernicusClient(timeout=17, opener=opener)
        with mock.patch("marblescape_copernicus.time.sleep") as sleep:
            value = client._open("request", 20)
        self.assertEqual(value, (b"ok", "text/plain"))
        self.assertEqual(sleep.call_args_list, [mock.call(0.5), mock.call(1.0)])

    def test_bad_request_is_not_retried_and_preserves_service_detail(self):
        error = urllib.error.HTTPError(
            "https://example.invalid", 400, "Bad Request", {},
            io.BytesIO(b'{"description":"Output width exceeds the pixel limit"}'),
        )
        opener = mock.Mock(side_effect=error)
        client = copernicus.CopernicusClient(opener=opener)
        with self.assertRaisesRegex(RuntimeError, "Output width exceeds the pixel limit"):
            client._open("request", 20)
        self.assertEqual(opener.call_count, 1)

    def test_default_opener_uses_current_ca_bundle_and_system_trust(self):
        response = _Response(b"ok", "text/plain")
        with mock.patch.object(
            copernicus.urllib.request, "urlopen", return_value=response
        ) as urlopen:
            client = copernicus.CopernicusClient()
            raw, content_type = client._open(
                copernicus.urllib.request.Request("https://example.test/value"), 10
            )
        self.assertEqual(raw, b"ok")
        self.assertEqual(content_type, "text/plain")
        self.assertIn("context", urlopen.call_args.kwargs)
        self.assertGreater(urlopen.call_args.kwargs["context"].cert_store_stats()["x509_ca"], 0)

    def test_gisco_network_failure_is_not_reported_as_copernicus_failure(self):
        opener = mock.Mock(side_effect=urllib.error.URLError("certificate failure"))
        client = copernicus.CopernicusClient(opener=opener)
        request = copernicus.urllib.request.Request(
            copernicus.OSM_BACKGROUND_URL.format(z=7, x=1, y=1)
        )
        with mock.patch("marblescape_copernicus.time.sleep"), \
             self.assertRaisesRegex(RuntimeError, "GISCO map service network request failed"):
            client._open(request, 20)
        self.assertEqual(opener.call_count, copernicus.NETWORK_ATTEMPTS)

    def test_catalog_request_accepts_stac_geojson(self):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return _Response(b'{"type":"FeatureCollection","features":[]}',
                             "application/geo+json;charset=utf-8")

        client = copernicus.CopernicusClient("id", "secret", timeout=17, opener=opener)
        client._token = "token"
        client._token_deadline = time.monotonic() + 60
        result = client._json_request(copernicus.CATALOG_URL, {"collections": ["sentinel-1-grd"]})

        self.assertEqual(result["features"], [])
        request, timeout = requests[0]
        self.assertEqual(timeout, 17)
        self.assertEqual(request.get_header("Accept"), "application/geo+json")
        self.assertEqual(request.get_header("Content-type"), "application/json")

    def test_latest_and_date_refresh_parse_distinct_response_and_paginate(self):
        client = copernicus.CopernicusClient("id", "secret")
        responses = [
            {"features": ["2026-09-10", "2026-09-12", "2026-09-11"], "context": {}},
            {"features": [
                {"properties": {"datetime": "2026-09-12T08:20:00Z"}},
                {"properties": {"datetime": "2026-09-12T10:40:30.123Z"}},
            ], "context": {}},
        ]
        payloads = []

        def request(_url, payload):
            payloads.append(dict(payload))
            return responses.pop(0)

        client._json_request = request
        cloud_profile = {**L2A_PROFILE, "max_cloud_cover": 15}
        frame = client.latest(cloud_profile, (1920, 1080))
        self.assertEqual(frame["date"], "2026-09-12")
        self.assertEqual(frame["timestamp"], "2026-09-12T10:40:30.123000Z")
        self.assertTrue(frame["latest"])
        self.assertEqual(payloads[0]["distinct"], "date")
        self.assertNotIn("distinct", payloads[1])
        self.assertTrue(all("eo:cloud_cover<=15" in payload["filter"] for payload in payloads))

        responses.extend([
            {"features": ["2026-09-10", "2026-09-12"], "context": {"next": 2}},
            {"features": [{"properties": {"datetime": "2026-09-11T10:00:00Z"}}],
             "context": {}},
        ])
        self.assertEqual(client.list_dates(cloud_profile, (1920, 1080)),
                         ["2026-09-12", "2026-09-11", "2026-09-10"])
        self.assertEqual(payloads[-1]["next"], 2)
        self.assertTrue(all("eo:cloud_cover<=15" in payload["filter"] for payload in payloads))
        self.assertEqual(copernicus._catalog_filter({"data_filter": {}}, 15), "")

    def test_date_refresh_queries_only_recent_dates_when_cache_exists(self):
        client = copernicus.CopernicusClient("id", "secret")
        payloads = []

        def request(_url, payload):
            payloads.append(payload)
            return {"features": ["2026-09-20"], "context": {}}

        client._json_request = request
        dates = client.list_dates(copernicus.DEFAULT_PROFILE, (1920, 1080),
                                  since="2026-09-19")
        self.assertEqual(dates, ["2026-09-20"])
        self.assertTrue(payloads[0]["datetime"].startswith("2026-09-05T00:00:00Z/"))

    def test_zero_cloud_limit_reports_no_matching_acquisition_clearly(self):
        client = copernicus.CopernicusClient("id", "secret")
        payloads = []

        def request(_url, payload):
            payloads.append(payload)
            return {"features": [], "context": {}}

        client._json_request = request
        profile = {**L2A_PROFILE, "latitude": 19.60508,
                   "longitude": -155.43457, "map_zoom": 11,
                   "lookback_days": 90, "max_cloud_cover": 0}
        with self.assertRaisesRegex(RuntimeError, "at most 0% cloud cover"):
            client.latest(profile, (1920, 1080))
        self.assertTrue(payloads)
        self.assertTrue(all("eo:cloud_cover<=0" in payload["filter"] for payload in payloads))

    def test_process_request_uses_gap_fill_window_rgba_evalscript_and_documented_size(self):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return _Response(_png((3, 2)), "image/png")

        client = copernicus.CopernicusClient("id", "secret", timeout=17, opener=opener)
        client._token = "token"
        client._token_deadline = time.monotonic() + 60
        profile = copernicus.normalize_profile({**L2A_PROFILE,
                                                "date": "2026-09-01", "max_cloud_cover": 15})
        frame = client.latest(profile, (3, 2))
        image, downloaded = client._process_tile(frame, [1, 2, 3, 4], 3, 2)
        self.assertEqual(image.size, (3, 2))
        self.assertEqual(downloaded, len(_png((3, 2))))
        request, timeout = requests[0]
        payload = json.loads(request.data)
        self.assertEqual(timeout, 17)
        self.assertEqual(payload["output"]["width"], 3)
        self.assertEqual(payload["output"]["height"], 2)
        self.assertEqual(payload["input"]["bounds"]["bbox"], [1, 2, 3, 4])
        data_filter = payload["input"]["data"][0]["dataFilter"]
        self.assertEqual(data_filter["timeRange"], {
            "from": "2026-08-19T00:00:00Z", "to": "2026-09-01T23:59:59Z"
        })
        self.assertEqual(data_filter["mosaickingOrder"], "mostRecent")
        self.assertEqual(data_filter["maxCloudCoverage"], 15)
        self.assertIn("dataMask", payload["evalscript"])

        for mode in ("single", "black"):
            mode_profile = copernicus.normalize_profile({
                **profile, "coverage_mode": mode
            })
            mode_frame = client.latest(mode_profile, (3, 2))
            client._process_tile(mode_frame, [1, 2, 3, 4], 3, 2)
            mode_filter = json.loads(requests[-1][0].data)["input"]["data"][0]["dataFilter"]
            self.assertEqual(mode_filter["timeRange"], {
                "from": "2026-09-01T00:00:00Z", "to": "2026-09-01T23:59:59Z"
            })
            self.assertEqual(mode_filter["maxCloudCoverage"], 15)

    def test_process_request_rejects_oversized_tile_before_network_access(self):
        client = copernicus.CopernicusClient(opener=mock.Mock())
        frame = {
            "layer": copernicus.get_layer(
                copernicus.get_product("DEFAULT-THEME", copernicus.DEFAULT_PROFILE["product"]),
                copernicus.DEFAULT_PROFILE["layer"],
            ),
            "date": "2026-09-01",
        }
        with self.assertRaisesRegex(ValueError, "between 1 and 2500"):
            client._process_tile(frame, [1, 2, 3, 4], 2501, 10)
        client._opener.assert_not_called()

    def test_large_process_output_is_split_at_2500_and_stitched_exactly(self):
        client = copernicus.CopernicusClient("id", "secret")
        profile = copernicus.normalize_profile({**copernicus.DEFAULT_PROFILE, "date": "2026-09-01"})
        frame = client.latest(profile, (2601, 2501))

        def tile(_frame, _bbox, width, height):
            return Image.new("RGBA", (width, height), (1, 2, 3, 255)), 1

        with mock.patch.object(client, "_process_tile", side_effect=tile) as process:
            image, downloaded = client._render_satellite(frame)
        self.assertEqual(image.size, (2601, 2501))
        self.assertEqual(downloaded, 4)
        self.assertEqual(process.call_count, 4)
        self.assertTrue(all(call.args[2] <= 2500 and call.args[3] <= 2500
                            for call in process.call_args_list))
        self.assertEqual(image.getpixel((2600, 2500)), (1, 2, 3, 255))

    def test_gap_fill_refines_missing_pixels_without_replacing_existing_imagery(self):
        client = copernicus.CopernicusClient("id", "secret")
        profile = copernicus.normalize_profile({
            **L2A_PROFILE, "date": "2026-09-01",
            "coverage_mode": "fill_gaps",
        })
        frame = client.latest(profile, (1024, 512))
        broad = Image.new("RGBA", (1024, 512), (0, 0, 0, 0))
        broad.paste((200, 0, 0, 255), (0, 0, 768, 512))

        def process(_frame, _bbox, width, height):
            if (width, height) == (1024, 512):
                return broad.copy(), 10
            return Image.new("RGBA", (width, height), (0, 180, 0, 255)), 5

        with mock.patch.object(client, "_process_tile", side_effect=process) as requests:
            image, downloaded = client._render_satellite(frame)

        self.assertEqual(requests.call_count, 2)
        self.assertEqual(requests.call_args_list[1].args[2:], (512, 512))
        left, top, world_size = client._view_pixels(frame)
        expected_bbox = client._mercator_bbox(left, top, world_size, 512, 0, 512, 512)
        self.assertEqual(requests.call_args_list[1].args[1], expected_bbox)
        self.assertEqual(image.getpixel((100, 100)), (200, 0, 0, 255))
        self.assertEqual(image.getpixel((700, 100)), (200, 0, 0, 255))
        self.assertEqual(image.getpixel((900, 100)), (0, 180, 0, 255))
        self.assertEqual(downloaded, 15)

    def test_gap_fill_keeps_genuine_no_data_when_smaller_request_is_empty(self):
        client = copernicus.CopernicusClient("id", "secret")
        profile = copernicus.normalize_profile({
            **L2A_PROFILE, "date": "2026-09-01",
            "coverage_mode": "fill_gaps",
        })
        frame = client.latest(profile, (1024, 512))
        empty = Image.new("RGBA", (1024, 512), (0, 0, 0, 0))
        with mock.patch.object(client, "_process_tile", side_effect=[
            (empty, 10),
            (Image.new("RGBA", (512, 512), (0, 0, 0, 0)), 5),
            (Image.new("RGBA", (512, 512), (0, 0, 0, 0)), 5),
        ]) as requests:
            image, downloaded = client._render_satellite(frame)
        self.assertEqual(requests.call_count, 3)
        self.assertEqual(image.getchannel("A").getextrema(), (0, 0))
        self.assertEqual(downloaded, 20)

    def test_empty_dh_mosaic_reports_unavailable_imagery(self):
        client = copernicus.CopernicusClient("id", "secret")
        product = copernicus.get_product("DEFAULT-THEME", "MARBLESCAPE::S1-DH-MONTHLY")
        profile = copernicus.normalize_profile({
            **copernicus.DEFAULT_PROFILE,
            "mission": "Sentinel-1 Mosaics", "product": product["id"],
            "layer": product["layers"][0]["id"], "date": "2026-08-01",
            "map_zoom": 2, "coverage_mode": "single",
        })
        frame = client.latest(profile, (64, 64))
        empty = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        with mock.patch.object(client, "_process_tile", return_value=(empty, 10)):
            with self.assertRaisesRegex(RuntimeError, "DH covers mainly polar regions"):
                client._render_satellite(frame)

    def test_14400_by_8640_output_is_partitioned_within_process_limit(self):
        client = copernicus.CopernicusClient("id", "secret")
        profile = copernicus.normalize_profile({
            **copernicus.DEFAULT_PROFILE, "date": "2026-09-01"
        })
        frame = client.latest(profile, (14400, 8640))
        parts = list(client._process_parts(frame))
        self.assertEqual(len(parts), 24)
        self.assertEqual(sum(width * height for _x, _y, width, height, _bbox in parts),
                         14400 * 8640)
        self.assertTrue(all(width <= 2500 and height <= 2500
                            for _x, _y, width, height, _bbox in parts))

    def test_date_line_view_wraps_without_out_of_range_process_bboxes(self):
        client = copernicus.CopernicusClient("id", "secret")
        product = next(item for item in copernicus.products("DEFAULT-THEME", "Sentinel-5P"))
        profile = copernicus.normalize_profile({
            **copernicus.DEFAULT_PROFILE, "mission": "Sentinel-5P",
            "product": product["id"], "layer": product["layers"][0]["id"],
            "date": "2026-09-01", "longitude": 179.9, "map_zoom": 4,
        })
        frame = client.latest(profile, (100, 40))
        bboxes = []

        def tile(_frame, bbox, width, height):
            bboxes.append((bbox, width, height))
            return Image.new("RGBA", (width, height), (1, 2, 3, 255)), 1

        with mock.patch.object(client, "_process_tile", side_effect=tile):
            image, _ = client._render_satellite(frame)
        self.assertEqual(image.size, (100, 40))
        self.assertEqual(sum(item[1] for item in bboxes), 100)
        self.assertTrue(all(
            -copernicus.WEB_MERCATOR_HALF_WORLD <= bbox[0] < bbox[2]
            <= copernicus.WEB_MERCATOR_HALF_WORLD for bbox, _width, _height in bboxes
        ))

    def test_gisco_tiles_above_native_zoom_are_overzoomed_without_invalid_url(self):
        requests = []

        def opener(request, timeout):
            requests.append(request.full_url)
            return _Response(_png((256, 256)), "image/png")

        client = copernicus.CopernicusClient(opener=opener)
        image, downloaded = client._cached_map_tile(copernicus.OSM_BACKGROUND_URL, 19, 2, 3)
        self.assertEqual(image.size, (256, 256))
        self.assertGreater(downloaded, 0)
        self.assertIn("/18/1/1.png", requests[0])
        sibling, sibling_downloaded = client._cached_map_tile(
            copernicus.OSM_BACKGROUND_URL, 19, 3, 3
        )
        self.assertEqual(sibling.size, (256, 256))
        self.assertEqual(sibling_downloaded, 0)
        self.assertEqual(len(requests), 1)

        border_requests = []

        def border_opener(request, timeout):
            border_requests.append(request.full_url)
            return _Response(b"", "application/vnd.mapbox-vector-tile")

        border_client = copernicus.CopernicusClient(opener=border_opener)
        border, border_downloaded = border_client._cached_border_tile(
            copernicus.GISCO_BORDERS_URL, 19, 2, 3
        )
        sibling_border, sibling_border_downloaded = border_client._cached_border_tile(
            copernicus.GISCO_BORDERS_URL, 19, 3, 3
        )
        self.assertEqual(border.size, (256, 256))
        self.assertEqual(sibling_border.size, (256, 256))
        self.assertEqual(border_downloaded, 0)
        self.assertEqual(sibling_border_downloaded, 0)
        self.assertEqual(len(border_requests), 1)
        self.assertIn("/18/1/1.pbf", border_requests[0])

    def test_map_tile_cache_retains_compressed_tiles_without_losing_pixels(self):
        client = copernicus.CopernicusClient()
        tile = Image.new("RGBA", (256, 256), (20, 90, 140, 255))
        tile.putpixel((10, 20), (255, 0, 0, 255))
        key = ("map", 5, 2, 3)
        client._store_tile(key, tile)
        encoded = client._tile_cache[key]
        self.assertIsInstance(encoded, bytes)
        self.assertLess(len(encoded), 256 * 256 * 4)
        self.assertEqual(client._load_cached_tile(key).getpixel((10, 20)),
                         (255, 0, 0, 255))
        self.assertEqual(copernicus.TILE_CACHE_LIMIT, 256)

    def test_nodata_background_and_labels_are_independent(self):
        client = copernicus.CopernicusClient("id", "secret")
        satellite = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        satellite.putpixel((0, 0), (255, 0, 0, 255))

        def render(_frame):
            return satellite.copy(), 10

        def map_overlay(_frame, template):
            color = (0, 180, 0, 255) if template == copernicus.OSM_BACKGROUND_URL else (0, 0, 255, 64)
            return Image.new("RGBA", (4, 4), color), 20

        with mock.patch.object(client, "_render_satellite", side_effect=render), \
             mock.patch.object(client, "_map_overlay", side_effect=map_overlay) as overlay, \
             mock.patch.object(client, "_draw_attribution"):
            black_frame = {"profile": dict(copernicus.DEFAULT_PROFILE,
                                             coverage_mode="black",
                                             map_labels=False)}
            black_png, black_downloaded = client.fetch_image(black_frame)
            self.assertEqual(overlay.call_count, 0)
            self.assertEqual(black_downloaded, 10)
            with Image.open(io.BytesIO(black_png)) as image:
                self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
                self.assertEqual(image.getpixel((3, 3)), (0, 0, 0))

            map_frame = {"profile": dict(copernicus.DEFAULT_PROFILE,
                                           coverage_mode="single",
                                           map_labels=True)}
            map_png, map_downloaded = client.fetch_image(map_frame)
            self.assertEqual(overlay.call_count, 3)
            self.assertEqual(map_downloaded, 70)
            self.assertEqual(overlay.call_args_list[0].args[1], copernicus.OSM_BACKGROUND_URL)
            self.assertEqual(overlay.call_args_list[1].args[1], copernicus.GISCO_BORDERS_URL)
            self.assertEqual(overlay.call_args_list[2].args[1], copernicus.OSM_LABELS_URL)
            with Image.open(io.BytesIO(map_png)) as image:
                self.assertNotEqual(image.getpixel((3, 3)), (0, 0, 0))

    def test_optional_map_failure_keeps_satellite_in_every_coverage_mode(self):
        client = copernicus.CopernicusClient("id", "secret")
        satellite = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        satellite.putpixel((0, 0), (255, 0, 0, 255))

        for mode in ("single", "fill_gaps", "black"):
            with self.subTest(mode=mode), \
                 mock.patch.object(
                     client, "_render_satellite", return_value=(satellite.copy(), 10)
                 ), \
                 mock.patch.object(
                     client, "_map_overlay", side_effect=RuntimeError("certificate failure")
                 ) as overlay:
                frame = {"profile": dict(
                    copernicus.DEFAULT_PROFILE, coverage_mode=mode, map_labels=True
                )}
                rendered, downloaded = client.fetch_image(frame)
                self.assertEqual(downloaded, 10)
                self.assertEqual(overlay.call_count, 1)
                self.assertEqual(len(client.last_render_warnings), 1)
                self.assertIn("satellite imagery was kept", client.last_render_warnings[0])
                with Image.open(io.BytesIO(rendered)) as image:
                    self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
                    self.assertEqual(image.getpixel((3, 3)), (0, 0, 0))


class RuntimeIntegrationTests(unittest.TestCase):
    def test_example_configuration_contains_valid_default_copernicus_profile(self):
        with app.DEFAULT_CONFIG_TEMPLATE_PATH.open("rb") as handle:
            config = tomllib.load(handle)
        provider, profiles = app.normalize_source_configuration(
            config["source"]["provider"], config["sources"]
        )
        self.assertEqual(provider, "eumetsat")
        self.assertEqual(profiles["copernicus"], copernicus.DEFAULT_PROFILE)
        self.assertEqual(config["copernicus"], {
            "client_id": "", "client_secret_protected": ""
        })

    def test_copernicus_render_plan_uses_exact_output_without_wms(self):
        with mock.patch.multiple(app, IMAGE_SOURCE="copernicus", WIDTH=3840,
                                 HEIGHT=2160, ASPECT_RATIO="16:9"):
            plan = app.prepare_runtime_render_plan({})
        self.assertEqual(plan[0:5], (3840, 2160, 3840, 2160, 1.0))
        self.assertEqual(plan[9], "copernicus")
        self.assertEqual(plan[10], [])

    def test_auth_serialization_never_writes_plaintext_secret(self):
        old = "[source]\nprovider = \"eumetsat\"\n"
        with mock.patch.object(app, "COPERNICUS_CLIENT_SECRET", "old"), \
             mock.patch.object(app, "COPERNICUS_CLIENT_SECRET_PROTECTED", "dpapi:old"), \
             mock.patch.object(app, "protect_client_secret", return_value="dpapi:encrypted") as protect:
            result = app.replace_copernicus_auth_configuration(
                old, {"client_id": "client", "client_secret": "plain-secret"}
            )
        protect.assert_called_once_with("plain-secret")
        self.assertNotIn("plain-secret", result)
        parsed = tomllib.loads(result)
        self.assertEqual(parsed["copernicus"], {
            "client_id": "client", "client_secret_protected": "dpapi:encrypted"
        })

    def test_copernicus_image_key_ignores_unused_wms_placement_fields(self):
        state = app.capture_loaded_configuration()
        state["IMAGE_SOURCE"] = "copernicus"
        state["COPERNICUS_CLIENT_ID"] = "client"
        state["COPERNICUS_CLIENT_SECRET"] = "secret"
        first = app.image_configuration_key(state)
        changed = dict(state, VIEW_MODE="crop", ZOOM=9, BACKGROUND_COLOR="#123456")
        self.assertEqual(first, app.image_configuration_key(changed))
        changed = dict(state)
        changed["SOURCE_PROFILES"] = {key: dict(value)
                                      for key, value in state["SOURCE_PROFILES"].items()}
        changed["SOURCE_PROFILES"]["copernicus"] = dict(
            changed["SOURCE_PROFILES"]["copernicus"], longitude=11.0
        )
        self.assertNotEqual(first, app.image_configuration_key(changed))


if __name__ == "__main__":
    unittest.main()
