"""Conditional catalogue requests without live provider access."""

import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError

from marblescape_catalogues import (
    CatalogueClient, NOAA_CATALOGUE_TTL_SECONDS, NOAA_FAILURE_BACKOFF_SECONDS,
    STARTUP_CATALOGUE_TTL_SECONDS, _CatalogueHttpCache,
)
from marblescape_eumetsat import EumetsatCatalogueClient, DECORATIONS_URL
from marblescape_slider import SliderClient, CATALOGUE_URL
from marblescape_worldview import WorldviewClient, CAPABILITIES_URL


class _Response(io.BytesIO):
    def __init__(self, body, headers):
        super().__init__(body)
        self.headers = headers

    def geturl(self):
        return self.url


class CatalogueHttpCacheTests(unittest.TestCase):
    def test_other_public_catalogues_use_persisted_startup_checks(self):
        class PublicCatalogue:
            catalogue_warning = ""

            def __init__(self, offline=False):
                self.offline = offline
                self.calls = []

            def refresh_all_catalogues(self, refresh=True, progress=None):
                self.calls.append(("refresh", refresh))
                if self.offline:
                    raise OSError("provider offline")
                return {"providers": 1, "areas": 1, "products": 1,
                        "resolution_options": 1, "errors": [], "warning": "",
                        "complete": True}

            def list_areas(self, provider, refresh=False):
                self.calls.append(("areas", provider, refresh))
                if self.offline:
                    raise OSError("provider offline")
                return [{"id": "area", "label": "Area"}]

            def list_products(self, provider, area_id, refresh=False):
                self.calls.append(("products", provider, area_id, refresh))
                if self.offline:
                    raise OSError("provider offline")
                return [{"id": "product", "resolutions": ["1x1"]}]

        class EumetsatCatalogue:
            catalogue_warning = ""

            def __init__(self, offline=False):
                self.offline = offline
                self.calls = []

            def catalogue(self, refresh=True):
                self.calls.append(refresh)
                if self.offline:
                    raise OSError("provider offline")
                return [{"satellite": "MTG", "layer": "true_color"}]

        def make_client(path, offline=False):
            sources = {key: PublicCatalogue(offline) for key in
                       ("noaa", "himawari", "slider", "worldview")}
            sources["eumetsat"] = EumetsatCatalogue(offline)
            return CatalogueClient(cache_path=path, **sources), sources

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogues.json"
            first, _online = make_client(path)
            self.assertTrue(first.refresh_all_catalogues(refresh=True)["complete"])
            for provider in ("himawari", "slider", "worldview", "eumetsat"):
                self.assertTrue(first.catalogue_cached_for_automatic_use(provider))

            second, offline = make_client(path, offline=True)
            self.assertTrue(second.refresh_all_catalogues(refresh=True, startup=True)["complete"])
            self.assertTrue(all(not source.calls for source in offline.values()))
            self.assertEqual(second.list_areas("worldview")[0]["id"], "area")
            self.assertEqual(second.list_products("slider", "area")[0]["id"], "product")
            self.assertTrue(all(not source.calls for source in offline.values()))

            second._cache._data["catalogue_checks"]["worldview"]["checked_at"] -= (
                STARTUP_CATALOGUE_TTL_SECONDS + 1
            )
            second._cache._save()
            self.assertFalse(second.refresh_all_catalogues(refresh=True, startup=True)["complete"])
            self.assertTrue(offline["worldview"].calls)
            self.assertFalse(offline["himawari"].calls)
            self.assertFalse(offline["slider"].calls)
            self.assertFalse(offline["eumetsat"].calls)

            third, still_offline = make_client(path, offline=True)
            self.assertFalse(third.refresh_all_catalogues(refresh=True, startup=True)["complete"])
            self.assertTrue(all(not source.calls for source in still_offline.values()))
            self.assertTrue(third.catalogue_cached_for_automatic_use("worldview"))
            third.list_areas("worldview", refresh=True)
            self.assertTrue(still_offline["worldview"].calls)

    def test_startup_reuses_complete_noaa_catalogue_until_it_expires(self):
        class PublicCatalogue:
            def __init__(self, offline=False):
                self.offline = offline
                self.calls = []
                self.catalogue_warning = ""

            def refresh_all_catalogues(self, refresh=True, progress=None):
                self.calls.append(("refresh", refresh))
                if self.offline:
                    raise OSError("NOAA offline")
                return {"providers": 3, "areas": 3, "products": 3,
                        "resolution_options": 3, "errors": [], "warning": "",
                        "complete": True}

            def list_areas(self, provider, refresh=False):
                self.calls.append(("areas", provider, refresh))
                if self.offline:
                    raise OSError("NOAA offline")
                return [{"id": "full_disk", "label": "Full Disk"}]

            def list_products(self, provider, area_id, refresh=False):
                self.calls.append(("products", provider, area_id, refresh))
                if self.offline:
                    raise OSError("NOAA offline")
                return [{"id": "GEOCOLOR", "label": "GeoColor",
                         "resolutions": ["678x678"]}]

        class OtherCatalogue:
            catalogue_warning = ""

            def refresh_all_catalogues(self, refresh=True, progress=None):
                return {"providers": 1, "areas": 1, "products": 1,
                        "resolution_options": 1, "errors": [], "warning": "",
                        "complete": True}

            def list_areas(self, provider, refresh=False):
                return [{"id": "area"}]

            def list_products(self, provider, area_id, refresh=False):
                return [{"id": "product", "resolutions": ["1x1"]}]

        class EumetsatCatalogue:
            catalogue_warning = ""

            def catalogue(self, refresh=True):
                return [{"satellite": "MTG"}]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogues.json"
            other = OtherCatalogue()
            eumetsat = EumetsatCatalogue()
            online = PublicCatalogue()
            first = CatalogueClient(
                noaa=online, himawari=other, slider=other, worldview=other,
                eumetsat=eumetsat, cache_path=path,
            )
            self.assertTrue(first.refresh_all_catalogues(refresh=True)["complete"])
            self.assertTrue(first._cache.noaa_fresh())

            offline = PublicCatalogue(offline=True)
            second = CatalogueClient(
                noaa=offline, himawari=other, slider=other, worldview=other,
                eumetsat=eumetsat, cache_path=path,
            )
            self.assertTrue(second.refresh_all_catalogues(refresh=True, startup=True)["complete"])
            self.assertEqual(second.list_areas("goes_east")[0]["id"], "full_disk")
            self.assertEqual(second.list_products("goes_west", "full_disk")[0]["id"], "GEOCOLOR")
            self.assertEqual(offline.calls, [])

            self.assertEqual(second.list_areas("goes_east", refresh=True)[0]["id"], "full_disk")
            self.assertTrue(offline.calls)
            second._cache._data["noaa_checked_at"] -= NOAA_CATALOGUE_TTL_SECONDS + 1
            second._cache._save()
            self.assertFalse(second._cache.noaa_fresh())
            self.assertFalse(second.refresh_all_catalogues(refresh=True, startup=True)["complete"])
            self.assertTrue(second._cache.noaa_retry_pending())

            still_offline = PublicCatalogue(offline=True)
            third = CatalogueClient(
                noaa=still_offline, himawari=other, slider=other, worldview=other,
                eumetsat=eumetsat, cache_path=path,
            )
            self.assertFalse(third.refresh_all_catalogues(refresh=True, startup=True)["complete"])
            self.assertEqual(still_offline.calls, [])
            self.assertEqual(third.list_areas("goes_east")[0]["id"], "full_disk")
            self.assertEqual(third.list_products("solar", "full_disk")[0]["id"], "GEOCOLOR")
            self.assertEqual(still_offline.calls, [])
            third.list_areas("goes_east", refresh=True)
            self.assertTrue(still_offline.calls)
            third._cache._data["noaa_retry_after"] -= NOAA_FAILURE_BACKOFF_SECONDS + 1
            self.assertFalse(third._cache.noaa_retry_pending())

    def test_noaa_outage_uses_disk_cache_without_repeating_network_calls(self):
        class OfflineNOAA:
            def __init__(self):
                self.calls = []

            def list_areas(self, provider, refresh=False):
                self.calls.append(("areas", provider, refresh))
                raise OSError("NOAA offline")

            def list_products(self, provider, area_id, refresh=False):
                self.calls.append(("products", provider, area_id, refresh))
                raise OSError("NOAA offline")

        with tempfile.TemporaryDirectory() as directory:
            noaa = OfflineNOAA()
            client = CatalogueClient(
                noaa=noaa, cache_path=Path(directory) / "catalogues.json", retries=1,
            )
            areas = [{"id": "full_disk", "label": "Full Disk", "category": "Global"}]
            products = [{"id": "GEOCOLOR", "label": "GeoColor", "resolutions": ["678x678"]}]
            client._cache.store_areas("goes_east", areas)
            client._cache.store_products("goes_east", "full_disk", products)
            self.assertEqual(client.list_areas("goes_east", refresh=True), areas)
            call_count = len(noaa.calls)
            self.assertTrue(client.catalogue_offline("goes_east"))
            self.assertEqual(client.list_areas("goes_east"), areas)
            self.assertEqual(client.list_products("goes_east", "full_disk"), products)
            self.assertEqual(len(noaa.calls), call_count)

    def test_public_catalogue_clients_reuse_unchanged_metadata(self):
        for client_type, url in ((SliderClient, CATALOGUE_URL),
                                 (WorldviewClient, CAPABILITIES_URL)):
            with self.subTest(client=client_type.__name__), \
                    tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "catalogue_http.json"
                response = _Response(b"catalogue body", {"Last-Modified": "Wed, 23 Sep 2026 12:00:00 GMT"})
                response.url = url
                first_opener = mock.Mock()
                first_opener.open.return_value = response
                first = client_type(opener=first_opener)
                first.metadata_cache = _CatalogueHttpCache(path)
                self.assertEqual(first._request(url, 100)[0], b"catalogue body")

                def unchanged(request, timeout):
                    self.assertEqual(request.get_header("If-modified-since"),
                                     "Wed, 23 Sep 2026 12:00:00 GMT")
                    raise HTTPError(request.full_url, 304, "Not Modified", {}, None)

                second_opener = mock.Mock()
                second_opener.open.side_effect = unchanged
                second = client_type(opener=second_opener)
                second.metadata_cache = _CatalogueHttpCache(path)
                self.assertEqual(second._request(url, 100)[0], b"catalogue body")

    def test_etag_reuses_saved_body_after_restart_on_http_304(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue_http.json"
            first = EumetsatCatalogueClient()
            first.metadata_cache = _CatalogueHttpCache(path)
            with mock.patch("marblescape_eumetsat.urlopen", return_value=_Response(
                    b'{"products": [1]}', {"ETag": '"revision-1"'})):
                self.assertEqual(first._json_get(DECORATIONS_URL), {"products": [1]})

            second = EumetsatCatalogueClient()
            second.metadata_cache = _CatalogueHttpCache(path)

            def unchanged(request, timeout):
                self.assertEqual(request.get_header("If-none-match"), '"revision-1"')
                raise HTTPError(request.full_url, 304, "Not Modified", {}, None)

            with mock.patch("marblescape_eumetsat.urlopen", side_effect=unchanged):
                self.assertEqual(second._json_get(DECORATIONS_URL), {"products": [1]})

    def test_response_without_validator_is_not_reused(self):
        cache = _CatalogueHttpCache()
        cache.store(DECORATIONS_URL, b'{"a": 1}', {"ETag": '"old"'})
        cache.store(DECORATIONS_URL, b'{"a": 2}', {})
        self.assertEqual(cache.headers(DECORATIONS_URL), {})
        self.assertIsNone(cache.response(DECORATIONS_URL, 100))


if __name__ == "__main__":
    unittest.main()
