#!/usr/bin/env python3
"""
Real-data integration tests for LiDARCraft (build_map.py).
Tests live OpenStreetMap Nominatim + pyproj coordinate resolution and full 2,048 x 2,048m
1m LiDAR + OSM MLV4 binary tile compilation on Glastonbury Tor & town.
"""

import os
import struct
import sys
import tempfile
import unittest
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import build_map


class TestLiDARCraftIntegration(unittest.TestCase):
    def test_01_nominatim_and_pyproj_crs_resolution(self):
        """Verify live OpenStreetMap Nominatim lookup and pyproj CRS projection across query types."""
        # 1. UK Place Name -> EPSG:27700
        glasto = build_map.resolve_location("Glastonbury")
        self.assertEqual(glasto["country_code"], "gb")
        self.assertEqual(glasto["crs"], "EPSG:27700")
        self.assertTrue(348000 <= glasto["easting"] <= 352000, f"Unexpected easting: {glasto['easting']}")
        self.assertTrue(137000 <= glasto["northing"] <= 141000, f"Unexpected northing: {glasto['northing']}")

        # 2. Public UK Postcode (Palace of Westminster: SW1A 0AA) -> EPSG:27700
        postcode = build_map.resolve_location("sw1a0aa")
        self.assertEqual(postcode["country_code"], "gb")
        self.assertEqual(postcode["crs"], "EPSG:27700")
        self.assertTrue(529000 <= postcode["easting"] <= 531000)
        self.assertTrue(178000 <= postcode["northing"] <= 181000)

        # 3. Google Maps URL with zoom level -> exact lat/lon & suggested size
        gmaps = build_map.resolve_location("https://www.google.com/maps/@51.1446,-2.6986,15z")
        self.assertAlmostEqual(gmaps["lat"], 51.1446, places=4)
        self.assertAlmostEqual(gmaps["lon"], -2.6986, places=4)
        self.assertEqual(gmaps["suggested_size_km"], 4)
        self.assertEqual(gmaps["crs"], "EPSG:27700")

        # 4. International Landmark -> Automatic WGS84 UTM Zone (EPSG:32632 for Chamonix)
        chamonix = build_map.resolve_location("Chamonix, France")
        self.assertEqual(chamonix["country_code"], "fr")
        self.assertEqual(chamonix["crs"], "EPSG:32632")
        self.assertTrue(330000 <= chamonix["easting"] <= 340000)
        self.assertTrue(5080000 <= chamonix["northing"] <= 5100000)

    def test_02_glastonbury_real_lidar_and_osm_mlv4_tile(self):
        """Compile the real 2,048 x 2,048m Glastonbury Tor & town tile and verify MLV4 binary contents."""
        origin_e, origin_n = 366000, 252384
        datum_shift = -28
        tx, tz = -8, 55  # Covers E=349616..351664, N=137696..139744 (Glastonbury Tor & Town)

        with tempfile.TemporaryDirectory() as tmp_tiles_dir:
            landmarks, summary = build_map.build_tile(
                tx=tx,
                tz=tz,
                origin_e=origin_e,
                origin_n=origin_n,
                datum_shift=datum_shift,
                tiles_out_dir=tmp_tiles_dir,
                crs_str="EPSG:27700",
                country_code="gb",
            )

            # Verify real OpenStreetMap landmarks were discovered inside the tile
            self.assertIn("glastonburytor", landmarks, "Glastonbury Tor landmark should be extracted from OSM")
            self.assertIn("thechalicewellandgardens", landmarks, "Chalice Well & Gardens should be extracted from OSM")

            bin_path = os.path.join(tmp_tiles_dir, f"tile_{tx}_{tz}.bin")
            self.assertTrue(os.path.exists(bin_path), "Compiled MLV4 binary tile must exist")

            expected_size = 16 + 7 * build_map.TILE_SIZE * build_map.TILE_SIZE
            self.assertEqual(os.path.getsize(bin_path), expected_size)

            with open(bin_path, "rb") as f:
                header = f.read(16)
                payload = f.read()

            magic, w, h, hdr_datum = struct.unpack(">4siii", header)
            self.assertEqual(magic, b"MLV4")
            self.assertEqual(w, 2048)
            self.assertEqual(h, 2048)
            self.assertEqual(hdr_datum, datum_shift)

            # Parse the 5 planar MLV4 arrays (elev_packed >i2, packed_surf u1, tree_bytes u1, bldg_packed >u2, extras u1)
            n = w * h
            off = 0
            elev_packed = np.frombuffer(payload, dtype=">i2", count=n, offset=off).reshape((h, w))
            off += 2 * n
            packed_surf = np.frombuffer(payload, dtype=np.uint8, count=n, offset=off).reshape((h, w))
            off += n
            tree_bytes = np.frombuffer(payload, dtype=np.uint8, count=n, offset=off).reshape((h, w))
            off += n
            bldg_packed = np.frombuffer(payload, dtype=">u2", count=n, offset=off).reshape((h, w))
            off += 2 * n
            extras = np.frombuffer(payload, dtype=np.uint8, count=n, offset=off).reshape((h, w))

            # 1. Verify real terrain elevation range (Somerset Levels ~5m AOD up to Glastonbury Tor ~158m AOD)
            y_mc = (elev_packed >> 1).astype(np.int32)
            elev_aod = y_mc + hdr_datum
            min_aod = int(np.min(elev_aod))
            max_aod = int(np.max(elev_aod))
            self.assertTrue(2 <= min_aod <= 12, f"Expected lowland edge ~5m AOD, got {min_aod}m")
            self.assertTrue(154 <= max_aod <= 162, f"Expected Glastonbury Tor peak ~158m AOD, got {max_aod}m")

            # 2. Verify exact Glastonbury Tor summit coordinate (E=351216, N=138612)
            e0 = origin_e + tx * 2048
            n1 = origin_n - tz * 2048
            tor_col = 351216 - e0
            tor_row = n1 - 138612
            tor_elev = int(elev_aod[tor_row, tor_col])
            self.assertTrue(153 <= tor_elev <= 161, f"Expected Glastonbury Tor summit ~158m AOD, got {tor_elev}m")

            # 3. Verify sub-metre 0.5m slab smoothing flag (bit 0 of elev_packed)
            slab_columns = int(np.count_nonzero(elev_packed & 1))
            self.assertGreater(slab_columns, 500_000, "Expected sub-metre 0.5m slab smoothing across terrain")

            # 4. Verify real 3D OSM buildings (walls, windows, doors, chimneys)
            wtype = (bldg_packed >> 12) & 0x07
            bldg_columns = int(np.count_nonzero(wtype > 0))
            window_columns = int(np.count_nonzero(wtype == 3))
            door_columns = int(np.count_nonzero(wtype == 4))
            chimney_columns = int(np.count_nonzero((wtype > 0) & ((extras & 0x80) == 0) & ((extras & 0x01) == 1)))
            self.assertGreater(bldg_columns, 100_000, "Expected >100,000 m^2 of 3D buildings in Glastonbury")
            self.assertGreater(window_columns, 5_000, "Expected window glass pane masks on building walls")
            self.assertGreater(door_columns, 500, "Expected road-facing doorways on buildings")
            self.assertGreater(chimney_columns, 50, "Expected 1m LiDAR-detected roof chimneys")

            # 5. Verify 1m LiDAR canopy trees (tree_h >= 4) and hedgerows (road_surf == 10)
            tree_heights = tree_bytes & 0x1F
            road_surf = (packed_surf >> 4) & 0x0F
            tree_columns = int(np.count_nonzero(tree_heights >= 4))
            hedge_columns = int(np.count_nonzero(road_surf == 10))
            self.assertGreater(tree_columns, 10_000, "Expected >10,000 canopy-detected trees in Glastonbury tile")
            self.assertGreater(hedge_columns, 5_000, "Expected >5,000 hedgerow columns in Glastonbury tile")


if __name__ == "__main__":
    unittest.main(verbosity=2)
