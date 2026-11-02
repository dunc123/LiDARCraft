#!/usr/bin/env python3
"""
build_map.py — LiDARCraft 1:1 Real-World LiDAR + OpenStreetMap Minecraft Map Builder (MLV4)

Supports any location on Earth via a single unified OpenStreetMap Nominatim + pyproj pipeline:
  1. UK / Global Postcode: python build_map.py "BA6 8YH" --size 4
  2. Google Maps URL:      python build_map.py "https://www.google.com/maps/@51.1446,-2.6986,15z"
  3. Lat,Lon Coordinates:  python build_map.py "51.1446,-2.6986" --size 4
  4. Any Global Address:   python build_map.py "1600 Pennsylvania Ave, Washington DC" --size 2
  5. Any Global Landmark:  python build_map.py "Chamonix, France" --size 4

Coordinate Reference Systems (via pyproj):
  - Great Britain (country_code='gb'): EPSG:27700 (Ordnance Survey British National Grid)
  - Worldwide:                         Automatic local WGS84 UTM zone (EPSG:326xx / EPSG:327xx) in metres
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import json
import math
import os
import re
import shutil
import struct
import threading
import time
import urllib.parse
import requests
import numpy as np
from PIL import Image, ImageDraw
from pyproj import Transformer
from pyproj.aoi import AreaOfInterest
from pyproj.database import query_utm_crs_info

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "tile_cache")
DEFAULT_SERVER_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "local_server"))
TILE_SIZE = 2048
OVERPASS_SEM = threading.Semaphore(1)
PRINT_LOCK = threading.Lock()


# ============================================================================
# 0. Live Multi-Stage Progress Tracker
# ============================================================================

class ProgressTracker:
    def __init__(self, total_tiles: int):
        self.total = max(1, total_tiles)
        self.completed = 0
        self.start_time = time.time()

    def stage(self, tx: int, tz: int, step: int, message: str):
        elapsed = time.time() - self.start_time
        with PRINT_LOCK:
            print(f"  -> [Tile ({tx},{tz}) | Step {step}/3 | {elapsed:4.1f}s] {message}", flush=True)

    def finish_tile(self, tx: int, tz: int, summary: str):
        with PRINT_LOCK:
            self.completed += 1
            elapsed = time.time() - self.start_time
            pct = int(round((self.completed / self.total) * 100))
            bar_len = 20
            filled = int(round(bar_len * self.completed / self.total))
            bar = "#" * filled + "-" * (bar_len - filled)
            if self.completed < self.total and self.completed > 0:
                eta = (elapsed / self.completed) * (self.total - self.completed)
                eta_str = f"ETA ~{eta:.0f}s"
            else:
                eta_str = "Complete"
            print(
                f"[{bar}] {self.completed}/{self.total} ({pct:3d}%) | {elapsed:4.1f}s | {eta_str} | {summary}",
                flush=True,
            )


# ============================================================================
# 1. PROJ Coordinate Reference System (pyproj) & Country Detection
# ============================================================================

_TRANSFORMER_CACHE = {}


def get_transformers(crs_str: str):
    """Return cached (to_metric, to_wgs84) pyproj Transformers for the given EPSG code."""
    if crs_str not in _TRANSFORMER_CACHE:
        to_xy = Transformer.from_crs("EPSG:4326", crs_str, always_xy=True)
        to_ll = Transformer.from_crs(crs_str, "EPSG:4326", always_xy=True)
        _TRANSFORMER_CACHE[crs_str] = (to_xy, to_ll)
    return _TRANSFORMER_CACHE[crs_str]


def select_crs_for_location(lat: float, lon: float, country_code: str) -> str:
    """
    Select the authoritative metric Coordinate Reference System (CRS) for a location:
      - 'gb' (Great Britain) -> EPSG:27700 (British National Grid)
      - Anywhere else        -> Local WGS84 UTM zone (EPSG:326xx / EPSG:327xx) via PROJ database
    """
    if country_code == "gb":
        return "EPSG:27700"
    utm_list = query_utm_crs_info(
        datum_name="WGS 84",
        area_of_interest=AreaOfInterest(lon, lat, lon, lat),
    )
    if utm_list:
        return f"EPSG:{utm_list[0].code}"
    zone = int((lon + 180.0) // 6.0) + 1
    return f"EPSG:{32600 + zone if lat >= 0 else 32700 + zone}"


# ============================================================================
# 2. Unified Location Resolver (Google Maps URL / Postcode / Lat,Lon / Address -> Nominatim + pyproj)
# ============================================================================

def resolve_location(query: str, force_crs: str = None):
    """
    Resolve any input (Google Maps URL, UK/global postcode, 'lat,lon', street address,
    or landmark) via a single unified OpenStreetMap Nominatim + pyproj pipeline.
    """
    q = query.strip()
    suggested_size_km = 4
    exact_latlon = None

    # 1. If input is a Google Maps URL, extract @lat,lon (and zoom) to pass to Nominatim
    if q.startswith("http://") or q.startswith("https://") or "google.com/maps" in q or "goo.gl" in q:
        url = q
        if "goo.gl" in url or "maps.app" in url:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True, timeout=15)
            url = r.url

        m_pin = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", url)
        m_at = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)(?:,(\d+(?:\.\d+)?)z)?", url)
        m_q = re.search(r"[?&](?:q|ll)=(-?\d+\.\d+),(-?\d+\.\d+)", url)

        if m_pin:
            exact_latlon = (float(m_pin.group(1)), float(m_pin.group(2)))
        elif m_at:
            exact_latlon = (float(m_at.group(1)), float(m_at.group(2)))
        elif m_q:
            exact_latlon = (float(m_q.group(1)), float(m_q.group(2)))

        if m_at and m_at.group(3):
            zoom = float(m_at.group(3))
            suggested_size_km = 2 if zoom >= 15.5 else (4 if zoom >= 13.8 else 6)

        if exact_latlon:
            q = f"{exact_latlon[0]},{exact_latlon[1]}"
        else:
            raise ValueError(f"Could not extract coordinates from Google Maps URL: {url}")

    # 2. If input is a raw 'lat,lon' pair, preserve exact coordinates while querying Nominatim for metadata
    m_pair = re.match(r"^\s*(-?\d+\.\d+)\s*[,;\s]\s*(-?\d+\.\d+)\s*$", q)
    if m_pair and exact_latlon is None:
        v1, v2 = float(m_pair.group(1)), float(m_pair.group(2))
        if -90.0 <= v1 <= 90.0 and -180.0 <= v2 <= 180.0:
            exact_latlon = (v1, v2)

    # Normalize compact UK postcodes (e.g. 'ba68yh' -> 'BA6 8YH') so Nominatim matches them directly
    q_search = (
        re.sub(r"^([A-Za-z]{1,2}\d[A-Za-z\d]?)\s*(\d[A-Za-z]{2})$", r"\1 \2", q).upper()
        if re.match(r"^[A-Za-z]{1,2}\d[A-Za-z\d]?\s*\d[A-Za-z]{2}$", q)
        else q
    )

    # 3. Single unified OpenStreetMap Nominatim lookup for all queries
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": q_search, "format": "json", "limit": 1, "addressdetails": 1},
        headers={"User-Agent": "LiDARCraft/1.0"},
        timeout=15,
    )
    if r.status_code == 200 and r.json():
        hit = r.json()[0]
        lat = exact_latlon[0] if exact_latlon else float(hit["lat"])
        lon = exact_latlon[1] if exact_latlon else float(hit["lon"])
        addr = hit.get("address") or {}
        cc = addr.get("country_code", "").lower()
        crs = force_crs or select_crs_for_location(lat, lon, cc)
        to_xy, _ = get_transformers(crs)
        e, n = to_xy.transform(lon, lat)

        locality = addr.get("village") or addr.get("town") or addr.get("suburb") or addr.get("city") or ""
        short_name = hit.get("name") or hit.get("display_name", q_search).split(",")[0]
        label = f"{short_name} ({locality})" if (locality and locality.lower() not in short_name.lower()) else short_name
        key = re.sub(r"[^a-z0-9]", "", short_name.lower())[:18] or "place"

        return {
            "key": key,
            "label": label,
            "easting": int(round(e)),
            "northing": int(round(n)),
            "lat": lat,
            "lon": lon,
            "country_code": cc,
            "crs": crs,
            "suggested_size_km": suggested_size_km,
        }

    raise ValueError(f"Could not resolve '{query}' via OpenStreetMap Nominatim.")


# ============================================================================
# 3. Multi-Country LiDAR & Global Elevation Providers
# ============================================================================

def fetch_aws_terrarium_dem(s_lat: float, w_lon: float, n_lat: float, e_lon: float, out_w=TILE_SIZE, out_h=TILE_SIZE):
    """
    Fetch high-precision elevation from AWS Open Data Terrain Tiles (Terrarium z=15).
    Works globally (100% of Earth), combining open national LiDAR (UK, US 3DEP, Switzerland,
    Austria, Norway, ArcticDEM) with Copernicus/SRTM at 0.0039m vertical precision.
    """
    z = 15
    n_tiles = 2 ** z

    def lon2x(lon):
        return (lon + 180.0) / 360.0 * n_tiles

    def lat2y(lat):
        lat_rad = math.radians(max(-85.0, min(85.0, lat)))
        return (1.0 - math.log(math.tan(lat_rad) + (1.0 / math.cos(lat_rad))) / math.pi) / 2.0 * n_tiles

    fx0, fx1 = lon2x(min(w_lon, e_lon)), lon2x(max(w_lon, e_lon))
    fy0, fy1 = lat2y(max(s_lat, n_lat)), lat2y(min(s_lat, n_lat))
    tx0, tx1 = int(math.floor(fx0)), int(math.floor(fx1))
    ty0, ty1 = int(math.floor(fy0)), int(math.floor(fy1))

    grid_w = (tx1 - tx0 + 1) * 256
    grid_h = (ty1 - ty0 + 1) * 256
    mosaic = np.zeros((grid_h, grid_w), dtype=np.float32)

    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            url = f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{tx % n_tiles}/{ty}.png"
            try:
                r = requests.get(url, timeout=30)
                if r.status_code == 200:
                    rgb = np.array(Image.open(io.BytesIO(r.content)).convert("RGB"), dtype=np.float32)
                    elev = (rgb[:, :, 0] * 256.0 + rgb[:, :, 1] + rgb[:, :, 2] / 256.0) - 32768.0
                    y_off, x_off = (ty - ty0) * 256, (tx - tx0) * 256
                    mosaic[y_off : y_off + 256, x_off : x_off + 256] = elev
            except Exception:
                pass

    px0 = int(round((fx0 - tx0) * 256))
    px1 = max(px0 + 1, int(round((fx1 - tx0) * 256)))
    py0 = int(round((fy0 - ty0) * 256))
    py1 = max(py0 + 1, int(round((fy1 - ty0) * 256)))
    cropped = mosaic[max(0, py0) : min(grid_h, py1), max(0, px0) : min(grid_w, px1)]
    if cropped.size == 0:
        return np.full((out_h, out_w), 50.0, dtype=np.float32)

    pil_f = Image.fromarray(cropped, mode="F").resize((out_w, out_h), resample=Image.Resampling.BICUBIC)
    return np.array(pil_f, dtype=np.float32)


def fetch_usgs_3dep_dem(s_lat: float, w_lon: float, n_lat: float, e_lon: float, out_w=TILE_SIZE, out_h=TILE_SIZE):
    """Fetch 1m LiDAR / 3DEP float32 GeoTIFF from the US Geological Survey National Map REST API."""
    url = (
        "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
        f"?bbox={min(w_lon, e_lon)},{min(s_lat, n_lat)},{max(w_lon, e_lon)},{max(s_lat, n_lat)}"
        f"&bboxSR=4326&size={out_w},{out_h}"
        "&imageSR=4326&format=tiff&pixelType=F32&noDataInterpretation=esriNoDataMatchAny&interpolation=+RSP_BilinearInterpolation&f=image"
    )
    r = requests.get(url, timeout=90)
    r.raise_for_status()
    arr = np.array(Image.open(io.BytesIO(r.content)), dtype=np.float32)
    bad = (~np.isfinite(arr)) | (arr < -500) | (arr > 9000)
    if np.all(bad):
        raise ValueError("USGS 3DEP returned no valid coverage for this bbox")
    if np.any(bad):
        arr[bad] = float(np.nanmedian(arr[~bad]))
    return arr


def fetch_uk_ea_lidar(dataset_uuid, coverage_id, e0, e1, n0, n1):
    url = (
        f"https://environment.data.gov.uk/geoservices/datasets/{dataset_uuid}/wcs"
        f"?service=WCS&version=2.0.1&request=GetCoverage"
        f"&coverageId={coverage_id}"
        f"&format=image/tiff"
        f"&subset=E({e0},{e1})&subset=N({n0},{n1})"
    )
    r = requests.get(url, timeout=90)
    r.raise_for_status()
    return np.array(Image.open(io.BytesIO(r.content)), dtype=np.float32)


def fetch_elevation_pair(e0, e1, n0, n1, crs_str: str, country_code: str, custom_dtm=None, custom_dsm=None):
    """
    Returns (dtm, dsm, has_true_lidar_dsm, provider_name).
    Dispatches based on CRS / ISO country_code with uniform coordinate-keyed caching.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    if custom_dtm and os.path.exists(custom_dtm):
        dtm = np.array(Image.open(custom_dtm).resize((TILE_SIZE, TILE_SIZE), Image.Resampling.BICUBIC), dtype=np.float32)
        if custom_dsm and os.path.exists(custom_dsm):
            dsm = np.array(Image.open(custom_dsm).resize((TILE_SIZE, TILE_SIZE), Image.Resampling.BICUBIC), dtype=np.float32)
            return dtm, dsm, True, "Custom GeoTIFF (DTM+DSM)"
        return dtm, dtm.copy(), False, "Custom GeoTIFF (DTM)"

    crs_tag = crs_str.replace(":", "")
    dtm_cache = os.path.join(CACHE_DIR, f"dtm_{crs_tag}_E{e0}_N{n0}.npy")
    dsm_cache = os.path.join(CACHE_DIR, f"dsm_{crs_tag}_E{e0}_N{n0}.npy")

    if os.path.exists(dtm_cache) and os.path.exists(dsm_cache):
        try:
            dtm = np.load(dtm_cache)
            dsm = np.load(dsm_cache)
            has_dsm = float(np.max(dsm - dtm)) > 0.5
            return dtm, dsm, has_dsm, "Cached 1m LiDAR"
        except Exception:
            pass

    _, to_ll = get_transformers(crs_str)
    w_lon, s_lat = to_ll.transform(e0, n0)
    e_lon, n_lat = to_ll.transform(e1, n1)

    # Provider 1: UK Environment Agency 1m DTM + 1m First-Return DSM (EPSG:27700)
    if crs_str == "EPSG:27700" or country_code == "gb":
        try:
            dtm = fetch_uk_ea_lidar(
                "13787b9a-26a4-4775-8523-806d13af58fc",
                "13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m",
                e0, e1, n0, n1,
            )
            dsm = fetch_uk_ea_lidar(
                "df4e3ec3-315e-48aa-aaaf-b5ae74d7b2bb",
                "df4e3ec3-315e-48aa-aaaf-b5ae74d7b2bb__Lidar_Composite_Elevation_FZ_DSM_1m",
                e0, e1, n0, n1,
            )
            bad_dtm = (~np.isfinite(dtm)) | (dtm < -50) | (dtm > 1500)
            if not np.all(bad_dtm):
                if np.any(bad_dtm):
                    fallback = fetch_aws_terrarium_dem(s_lat, w_lon, n_lat, e_lon)
                    dtm[bad_dtm] = fallback[bad_dtm]
                bad_dsm = (~np.isfinite(dsm)) | (dsm < -50) | (dsm > 1500)
                if np.any(bad_dsm):
                    dsm[bad_dsm] = dtm[bad_dsm]
                np.save(dtm_cache, dtm)
                np.save(dsm_cache, dsm)
                return dtm, dsm, True, "UK Environment Agency 1m LiDAR (DTM+DSM)"
        except Exception:
            pass

    # Provider 2: United States USGS 3DEP (1m LiDAR / NED)
    if country_code == "us":
        try:
            dtm = fetch_usgs_3dep_dem(s_lat, w_lon, n_lat, e_lon)
            np.save(dtm_cache, dtm)
            np.save(dsm_cache, dtm)
            return dtm, dtm.copy(), False, "USGS 3DEP Elevation + OSM 3D Synthesis"
        except Exception:
            pass

    # Provider 3: Global AWS Terrarium Elevation Tiles (Worldwide Fallback)
    dtm = fetch_aws_terrarium_dem(s_lat, w_lon, n_lat, e_lon)
    np.save(dtm_cache, dtm)
    np.save(dsm_cache, dtm)
    return dtm, dtm.copy(), False, "Global Terrain Tiles (Terrarium z=15) + OSM 3D Synthesis"


def fetch_osm_tile(e0, e1, n0, n1, crs_str: str):
    os.makedirs(CACHE_DIR, exist_ok=True)
    crs_tag = crs_str.replace(":", "")
    coord_cache = os.path.join(CACHE_DIR, f"osm_{crs_tag}_E{e0}_N{n0}.json")

    if os.path.exists(coord_cache):
        try:
            with open(coord_cache, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("elements"):
                return data
        except Exception:
            pass

    _, to_ll = get_transformers(crs_str)
    w_lon, s_lat = to_ll.transform(e0 - 150, n0 - 150)
    e_lon, n_lat = to_ll.transform(e1 + 150, n1 + 150)
    bbox = f"({min(s_lat, n_lat):.5f},{min(w_lon, e_lon):.5f},{max(s_lat, n_lat):.5f},{max(w_lon, e_lon):.5f})"
    query = (
        f'[out:json][timeout:60];('
        f'node["place"]{bbox};node["natural"="peak"]{bbox};node["natural"="saddle"]{bbox};node["natural"="tree"]{bbox};'
        f'way["highway"]{bbox};way["railway"="rail"]{bbox};way["building"]{bbox};'
        f'way["barrier"]{bbox};way["natural"]{bbox};way["landuse"]{bbox};'
        f'way["leisure"]{bbox};way["amenity"="parking"]{bbox};way["waterway"]{bbox};'
        f');out body;>;out skel qt;'
    )
    headers = {"User-Agent": "LiDARCraft/1.0", "Accept": "*/*"}
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    ]
    with OVERPASS_SEM:
        for _ in range(2):
            for ep in endpoints:
                try:
                    r = requests.get(ep, params={"data": query}, headers=headers, timeout=60)
                    if r.status_code == 200:
                        data = r.json()
                        with open(coord_cache, "w", encoding="utf-8") as f:
                            json.dump(data, f)
                        return data
                except Exception:
                    pass
            time.sleep(1.5)
    return {"elements": []}


def max_filter_2d(arr, radius):
    h, w = arr.shape
    padded_x = np.pad(arr, ((0, 0), (radius, radius)), mode="constant", constant_values=0.0)
    max_x = np.zeros_like(arr)
    for dx in range(2 * radius + 1):
        max_x = np.maximum(max_x, padded_x[:, dx : dx + w])
    padded_y = np.pad(max_x, ((radius, radius), (0, 0)), mode="constant", constant_values=0.0)
    max_xy = np.zeros_like(arr)
    for dy in range(2 * radius + 1):
        max_xy = np.maximum(max_xy, padded_y[dy : dy + h, :])
    return max_xy


def resolve_building_style(tags, bid):
    btype = tags.get("building", "yes").lower()
    mat = tags.get("building:material", "").lower()
    col = tags.get("building:colour", "").lower()
    name = tags.get("name", "").lower()

    if "sandstone" in mat or "limestone" in mat or btype in ("church", "cathedral", "chapel", "castle") or any(
        k in name for k in ("church", "priory", "abbey", "chapel", "castle", "manor", "court")
    ):
        return 4  # Historic Sandstone / Stone
    if "brick" in mat or "red" in col or btype in ("school", "college", "hotel", "train_station", "civic"):
        return 2  # Red Brick
    if "white" in col or "cream" in col or "plaster" in mat or "render" in mat:
        return 1  # White Stucco / Render
    if "stone" in mat or "granite" in mat or "cotswold" in mat:
        return 3  # Natural Stone
    if btype in ("retail", "commercial", "supermarket", "office", "industrial", "warehouse"):
        return 5  # Commercial Stone/Andesite

    hv = (bid * 2654435761) % 100
    return 1 if hv < 40 else (2 if hv < 75 else 3)


def build_tile(
    tx, tz, origin_e, origin_n, datum_shift, tiles_out_dir,
    crs_str="EPSG:27700", country_code="gb", custom_dtm=None, custom_dsm=None, tracker: ProgressTracker = None
):
    os.makedirs(tiles_out_dir, exist_ok=True)
    out_file = os.path.join(tiles_out_dir, f"tile_{tx}_{tz}.bin")
    e0 = origin_e + tx * TILE_SIZE
    e1 = e0 + TILE_SIZE
    n1 = origin_n - tz * TILE_SIZE
    n0 = n1 - TILE_SIZE

    if tracker:
        tracker.stage(tx, tz, 1, f"Fetching elevation grid (E={e0}..{e1}, N={n0}..{n1})...")
    dtm, dsm, has_true_dsm, provider_name = fetch_elevation_pair(
        e0, e1, n0, n1, crs_str, country_code, custom_dtm, custom_dsm
    )

    if tracker:
        tracker.stage(tx, tz, 2, "Querying OpenStreetMap vectors (buildings, roads, landuse, trees)...")
    osm = fetch_osm_tile(e0, e1, n0, n1, crs_str)
    to_xy, _ = get_transformers(crs_str)

    h, w = dtm.shape
    chm = np.maximum(0.0, dsm - dtm)
    dy, dx = np.gradient(dtm, 1.0, 1.0)
    slope_mag = np.sqrt(dx * dx + dy * dy)

    # Relative prominence above local valley baseline (altitude-agnostic across any elevation on Earth)
    valley_base_m = float(np.percentile(dtm, 15))
    rel_prominence = np.maximum(0.0, dtm - valley_base_m)

    exact_y = dtm - datum_shift
    y_floor = np.floor(exact_y)
    half_step = ((exact_y - y_floor) >= 0.5).astype(np.int16)
    y_mc = np.clip(y_floor, -60, 310).astype(np.int16)
    elev_packed = (y_mc << 1) | half_step

    osm_tree_nodes = []
    discovered_landmarks = {}

    raw_nodes = [el for el in osm.get("elements", []) if el["type"] == "node"]
    ways = [el for el in osm.get("elements", []) if el["type"] == "way"]
    nodes = {}

    if raw_nodes:
        lons = np.array([el["lon"] for el in raw_nodes], dtype=np.float64)
        lats = np.array([el["lat"] for el in raw_nodes], dtype=np.float64)
        es, ns = to_xy.transform(lons, lats)
        pxs = es - e0
        pys = n1 - ns

        for i, el in enumerate(raw_nodes):
            px, py = float(pxs[i]), float(pys[i])
            nodes[el["id"]] = (px, py)
            tags = el.get("tags")
            if tags:
                if tags.get("natural") == "tree" and (4 <= px < w - 4) and (4 <= py < h - 4):
                    osm_tree_nodes.append((int(round(px)), int(round(py))))
                name = tags.get("name")
                if name and (0 <= px < w) and (0 <= py < h):
                    if "place" in tags or tags.get("natural") in ("peak", "saddle") or "historic" in tags:
                        key = re.sub(r"[^a-z0-9]", "", name.lower())
                        if key:
                            discovered_landmarks[key] = {
                                "key": key,
                                "label": f"{name} ({tags['ele']}m)" if "ele" in tags else name,
                                "easting": int(round(es[i])),
                                "northing": int(round(ns[i])),
                            }

    paved_img = Image.new("L", (w, h), 0)
    paths_img = Image.new("L", (w, h), 0)
    rail_img = Image.new("L", (w, h), 0)
    barrier_img = Image.new("L", (w, h), 0)
    water_img = Image.new("L", (w, h), 0)
    wood_img = Image.new("L", (w, h), 0)
    conif_img = Image.new("L", (w, h), 0)
    heath_img = Image.new("L", (w, h), 0)
    park_img = Image.new("L", (w, h), 0)
    parking_img = Image.new("L", (w, h), 0)
    farmland_img = Image.new("L", (w, h), 0)
    meadow_img = Image.new("L", (w, h), 0)
    orchard_img = Image.new("L", (w, h), 0)
    bldg_id_img = Image.new("I", (w, h), 0)
    bldg_wall_img = Image.new("I", (w, h), 0)

    draw_paved = ImageDraw.Draw(paved_img)
    draw_paths = ImageDraw.Draw(paths_img)
    draw_rail = ImageDraw.Draw(rail_img)
    draw_barrier = ImageDraw.Draw(barrier_img)
    draw_water = ImageDraw.Draw(water_img)
    draw_wood = ImageDraw.Draw(wood_img)
    draw_conif = ImageDraw.Draw(conif_img)
    draw_heath = ImageDraw.Draw(heath_img)
    draw_park = ImageDraw.Draw(park_img)
    draw_parking = ImageDraw.Draw(parking_img)
    draw_farmland = ImageDraw.Draw(farmland_img)
    draw_meadow = ImageDraw.Draw(meadow_img)
    draw_orchard = ImageDraw.Draw(orchard_img)
    draw_bldg_id = ImageDraw.Draw(bldg_id_img)
    draw_bldg_wall = ImageDraw.Draw(bldg_wall_img)

    bldg_meta = {}
    bridge_ways = []
    lamp_positions = []
    next_bldg_id = 1

    for way in ways:
        tags = way.get("tags", {})
        pts = [nodes[nid] for nid in way.get("nodes", []) if nid in nodes]
        if len(pts) < 2:
            continue

        name = tags.get("name")
        if name and ("historic" in tags or tags.get("tourism") in ("attraction", "viewpoint") or tags.get("railway") == "station"):
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            if 0 <= cx < w and 0 <= cy < h:
                key = re.sub(r"[^a-z0-9]", "", name.lower())
                if key and key not in discovered_landmarks:
                    discovered_landmarks[key] = {
                        "key": key,
                        "label": name,
                        "easting": int(round(e0 + cx)),
                        "northing": int(round(n1 - cy)),
                    }

        hw = tags.get("highway")
        is_bridge = tags.get("bridge") in ("yes", "viaduct", "aqueduct") and tags.get("layer", "1") != "-1"

        if hw:
            wpx = 2
            if hw in ("motorway", "trunk", "primary", "secondary", "motorway_link", "trunk_link", "primary_link", "secondary_link"):
                wpx = 9 if hw in ("motorway", "trunk", "primary") else 7
                draw_paved.line(pts, fill=255, width=wpx, joint="curve")
            elif hw in ("tertiary", "unclassified", "residential", "living_street", "service"):
                wpx = 5 if hw != "service" else 3
                draw_paved.line(pts, fill=200, width=wpx, joint="curve")
            elif hw in ("track", "bridleway", "cycleway"):
                wpx = 3
                draw_paths.line(pts, fill=180, width=wpx, joint="curve")
            elif hw in ("footway", "path", "steps", "pedestrian"):
                wpx = 2
                draw_paths.line(pts, fill=255, width=wpx, joint="curve")

            if is_bridge:
                bridge_ways.append((pts, max(3, wpx)))

            if tags.get("lit") == "yes" or hw in ("residential", "primary", "secondary", "tertiary"):
                spacing = 24.0
                offset_dist = (wpx / 2.0) + 1.2
                acc = 0.0
                for i in range(len(pts) - 1):
                    x0, y0 = pts[i]
                    x1, y1 = pts[i + 1]
                    seg_len = math.hypot(x1 - x0, y1 - y0)
                    if seg_len < 1.0:
                        continue
                    ux, uy = (x1 - x0) / seg_len, (y1 - y0) / seg_len
                    nx, ny = -uy, ux
                    while acc < seg_len:
                        lx = int(round(x0 + ux * acc + nx * offset_dist))
                        ly = int(round(y0 + uy * acc + ny * offset_dist))
                        if 2 <= lx < w - 2 and 2 <= ly < h - 2:
                            lamp_positions.append((lx, ly))
                        acc += spacing
                    acc -= seg_len

        if tags.get("railway") == "rail":
            draw_rail.line(pts, fill=255, width=3, joint="curve")
            if is_bridge:
                bridge_ways.append((pts, 4))

        if "building" in tags and len(pts) >= 3 and next_bldg_id < 65000:
            bid = next_bldg_id
            next_bldg_id += 1
            if pts[0] != pts[-1]:
                pts = pts + [pts[0]]
            draw_bldg_id.polygon(pts, fill=bid)
            draw_bldg_wall.line(pts, fill=bid, width=2)
            bldg_meta[bid] = {
                "style": resolve_building_style(tags, bid),
                "levels": tags.get("building:levels"),
                "height": tags.get("height"),
                "pts": pts,
            }

        barrier = tags.get("barrier")
        if barrier in ("wall", "retaining_wall", "city_wall"):
            draw_barrier.line(pts, fill=200, width=1)
        elif barrier == "hedge" or tags.get("natural") == "tree_row":
            draw_barrier.line(pts, fill=255, width=2)
        elif barrier in ("fence", "handrail"):
            draw_barrier.line(pts, fill=150, width=1)

        ww = tags.get("waterway")
        if ww in ("river", "canal"):
            draw_water.line(pts, fill=255, width=8, joint="curve")
        elif ww in ("stream", "ditch", "drain"):
            draw_water.line(pts, fill=255, width=2)

        if len(pts) >= 3:
            poly = pts if pts[0] == pts[-1] else pts + [pts[0]]
            nat = tags.get("natural", "")
            lu = tags.get("landuse", "")
            leis = tags.get("leisure", "")
            if nat == "water" or lu in ("reservoir", "basin"):
                draw_water.polygon(poly, fill=255)
            if nat == "wood" or lu == "forest":
                draw_wood.polygon(poly, fill=255)
                if tags.get("leaf_type") == "needleleaved":
                    draw_conif.polygon(poly, fill=255)
            if nat in ("heath", "scrub", "grassland", "moor", "fell", "tundra"):
                draw_heath.polygon(poly, fill=255)
            if leis in ("park", "garden", "pitch", "golf_course", "common", "nature_reserve") or lu in ("recreation_ground", "village_green", "cemetery"):
                draw_park.polygon(poly, fill=255)
            if lu == "farmland":
                draw_farmland.polygon(poly, fill=255)
            if lu in ("meadow", "grass"):
                draw_meadow.polygon(poly, fill=255)
            if lu == "orchard":
                draw_orchard.polygon(poly, fill=255)
            if tags.get("amenity") == "parking":
                draw_parking.polygon(poly, fill=255)

    if tracker:
        tracker.stage(tx, tz, 3, f"Compiling {len(bldg_meta):,} 3D buildings, canopy trees & MLV4 binary...")

    paved_arr = np.array(paved_img)
    paths_arr = np.array(paths_img)
    rail_arr = np.array(rail_img)
    barrier_arr = np.array(barrier_img)
    water_mask = np.array(water_img) > 128
    wood_mask = np.array(wood_img) > 128
    conif_mask = np.array(conif_img) > 128
    heath_mask = np.array(heath_img) > 128
    park_mask = np.array(park_img) > 128
    parking_mask = np.array(parking_img) > 128
    farmland_mask = np.array(farmland_img) > 128
    meadow_mask = np.array(meadow_img) > 128
    orchard_mask = np.array(orchard_img) > 128
    id_arr = np.array(bldg_id_img, dtype=np.int32)
    wall_arr = np.array(bldg_wall_img, dtype=np.int32)

    if not has_true_dsm:
        yy_s, xx_s = np.meshgrid(np.arange(h, dtype=np.int32), np.arange(w, dtype=np.int32), indexing="ij")
        synth_hash = ((xx_s * 73428767 ^ yy_s * 912931) & 0x7FFFFFFF) % 100
        wood_trees = wood_mask & ((xx_s % 6 == 0) & (yy_s % 6 == 0)) & (synth_hash < 65)
        chm[wood_trees] = 9.0 + (synth_hash[wood_trees] % 9)
        for tx_n, ty_n in osm_tree_nodes:
            chm[ty_n, tx_n] = 10.0

    road_any_mask = (paved_arr > 0) | (paths_arr > 0) | (rail_arr > 0)

    lamp_arr = np.zeros((h, w), dtype=bool)
    for lx, ly in lamp_positions:
        if not road_any_mask[ly, lx] and id_arr[ly, lx] == 0:
            lamp_arr[ly, lx] = True

    bldg_packed = np.zeros((h, w), dtype=np.uint16)
    extras = np.zeros((h, w), dtype=np.uint8)
    bldg_buf_mask = max_filter_2d((id_arr > 0).astype(np.float32), 2) > 0

    if next_bldg_id > 1:
        door_mask = np.zeros((h, w), dtype=bool)
        corner_mask = np.zeros((h, w), dtype=bool)
        window_mask = np.zeros((h, w), dtype=bool)

        for bid, meta in bldg_meta.items():
            pts = meta["pts"]
            best_door = None
            best_road_dist = 999.0

            for i in range(len(pts) - 1):
                x0, y0 = pts[i]
                x1, y1 = pts[i + 1]
                ix0, iy0 = int(round(x0)), int(round(y0))
                if 0 <= ix0 < w and 0 <= iy0 < h:
                    corner_mask[iy0, ix0] = True

                seg_len = math.hypot(x1 - x0, y1 - y0)
                if seg_len < 3.5:
                    continue
                ux, uy = (x1 - x0) / seg_len, (y1 - y0) / seg_len
                steps = int(seg_len)
                for s in range(2, steps - 1):
                    px = int(round(x0 + ux * s))
                    py = int(round(y0 + uy * s))
                    if 2 <= px < w - 2 and 2 <= py < h - 2:
                        if s % 3 == 0:
                            window_mask[py, px] = True
                        if s == steps // 2:
                            r0, r1 = max(0, py - 8), min(h, py + 9)
                            c0, c1 = max(0, px - 8), min(w, px + 9)
                            if np.any(road_any_mask[r0:r1, c0:c1]):
                                d = seg_len
                                if d < best_road_dist:
                                    best_road_dist = d
                                    best_door = (px, py)
                            elif best_door is None:
                                best_door = (px, py)

            if best_door is not None:
                dx_p, dy_p = best_door
                door_mask[max(0, dy_p - 1) : min(h, dy_p + 2), max(0, dx_p - 1) : min(w, dx_p + 2)] = True
                window_mask[max(0, dy_p - 1) : min(h, dy_p + 2), max(0, dx_p - 1) : min(w, dx_p + 2)] = False

        flat_ids = id_arr.ravel()
        flat_dtm = dtm.ravel()
        flat_chm = chm.ravel()
        order = np.argsort(flat_ids)
        sorted_ids = flat_ids[order]
        sorted_dtm = flat_dtm[order]
        sorted_chm = flat_chm[order]

        unique_ids, start_idx, counts = np.unique(sorted_ids, return_index=True, return_counts=True)
        floor_lut = np.zeros(next_bldg_id, dtype=np.int16)
        eave_lut = np.zeros(next_bldg_id, dtype=np.int16)
        style_lut = np.zeros(next_bldg_id, dtype=np.uint8)

        for u_id, st, ct in zip(unique_ids, start_idx, counts):
            if u_id == 0:
                continue
            b_dtm = sorted_dtm[st : st + ct]
            b_chm = sorted_chm[st : st + ct]
            fl_m = float(np.percentile(b_dtm, 20))
            floor_lut[u_id] = int(np.clip(np.floor(fl_m - datum_shift), -60, 300))

            meta = bldg_meta.get(int(u_id), {"style": 1, "levels": None, "height": None})
            style_lut[u_id] = meta["style"]

            valid_c = b_chm[(b_chm >= 2.2) & (b_chm <= 35.0)]
            if len(valid_c) >= 4:
                eh = int(np.clip(np.round(np.percentile(valid_c, 35)), 3, 28))
            elif meta.get("height"):
                m_h = re.search(r"(\d+(?:\.\d+)?)", str(meta["height"]))
                eh = int(np.clip(round(float(m_h.group(1))), 3, 28)) if m_h else 5
            elif meta["levels"] and str(meta["levels"]).isdigit():
                eh = int(np.clip(int(meta["levels"]) * 3, 3, 28))
            else:
                eh = 5
            if ct > 450 and meta["style"] != 4:
                style_lut[u_id] = 5
            eave_lut[u_id] = eh

        has_b = id_arr > 0
        elev_packed[has_b] = (floor_lut[id_arr[has_b]] << 1)

        eave_2d = eave_lut[id_arr]
        raw_roof = np.where(chm >= 2.2, chm, eave_2d.astype(np.float32) + (2.0 if not has_true_dsm else 0.0))
        roof_h = np.clip(np.floor(raw_roof), eave_2d, np.minimum(45, eave_2d + 10)).astype(np.uint16)
        roof_half = ((raw_roof - np.floor(raw_roof)) >= 0.5).astype(np.uint8)

        chm_local_max = max_filter_2d(chm, 2)
        chm_local_min = -max_filter_2d(-chm, 2)
        is_chimney = (
            has_b
            & (wall_arr == 0)
            & (chm >= eave_2d + 1.5)
            & (chm == chm_local_max)
            & ((chm - chm_local_min) >= 1.2)
            & (chm <= eave_2d + 12.0)
        )

        is_wall = (wall_arr > 0) & has_b
        wtype = np.zeros((h, w), dtype=np.uint16)
        wtype[has_b & (~is_wall)] = 1  # interior
        wtype[is_wall] = 2             # plain wall
        wtype[is_wall & window_mask & (~corner_mask)] = 3  # window column
        wtype[is_wall & corner_mask] = 5                   # corner pillar
        wtype[is_wall & door_mask & (~corner_mask)] = 4    # doorway

        bldg_packed[has_b] = (
            ((wtype[has_b] & 0x07) << 12)
            | ((eave_2d[has_b].astype(np.uint16) & 0x3F) << 6)
            | (roof_h[has_b] & 0x3F)
        )
        extras[has_b] = (
            ((style_lut[id_arr[has_b]] & 0x07) << 2)
            | ((roof_half[has_b] & 0x01) << 1)
            | (is_chimney[has_b].astype(np.uint8) & 0x01)
        )

    if bridge_ways:
        bridge_deck_img = Image.new("I", (w, h), 0)
        bridge_edge_img = Image.new("L", (w, h), 0)
        draw_bdeck = ImageDraw.Draw(bridge_deck_img)
        draw_bedge = ImageDraw.Draw(bridge_edge_img)

        for pts, wpx in bridge_ways:
            x0, y0 = int(np.clip(pts[0][0], 0, w - 1)), int(np.clip(pts[0][1], 0, h - 1))
            x1, y1 = int(np.clip(pts[-1][0], 0, w - 1)), int(np.clip(pts[-1][1], 0, h - 1))
            deck_y = int(max(y_mc[y0, x0], y_mc[y1, x1]) + 1)
            draw_bedge.line(pts, fill=255, width=wpx + 2, joint="curve")
            draw_bdeck.line(pts, fill=deck_y + 1000, width=wpx, joint="curve")

        bdeck_arr = np.array(bridge_deck_img, dtype=np.int32)
        bedge_arr = np.array(bridge_edge_img, dtype=np.uint8) > 0
        has_bridge = bdeck_arr > 0
        if np.any(has_bridge):
            deck_abs_y = bdeck_arr - 1000
            offset_y = np.clip(deck_abs_y - y_mc, 1, 60).astype(np.uint8)
            is_parapet = bedge_arr & (~(bdeck_arr > 0))
            extras[has_bridge] = 0x80 | (is_parapet[has_bridge].astype(np.uint8) << 6) | (offset_y[has_bridge] & 0x3F)

    # Trees from 1m LiDAR CHM + Orchards + OSM Woodland synthesis
    valid_tree = (
        (chm >= 3.5)
        & (chm <= 28.0)
        & (~bldg_buf_mask)
        & (~road_any_mask)
        & (~water_mask)
        & (~parking_mask)
        & (slope_mag < 0.78)
    )
    yy, xx = np.meshgrid(np.arange(h, dtype=np.float32), np.arange(w, dtype=np.float32), indexing="ij")
    jitter = ((np.sin(xx * 12.9898 + yy * 78.233) * 43758.5453) % 1.0) * 0.02
    chm_work = np.where(valid_tree, chm + jitter, 0.0)
    lmax5 = max_filter_2d(chm_work, 2)
    lmax7 = max_filter_2d(chm_work, 3)
    is_tree = (valid_tree & (chm < 9.0) & (chm_work == lmax5) & (chm_work > 0)) | (
        valid_tree & (chm >= 9.0) & (chm_work == lmax7) & (chm_work > 0)
    )
    orchard_grid = orchard_mask & (~bldg_buf_mask) & (~road_any_mask) & ((xx.astype(np.int32) % 7 == 0) & (yy.astype(np.int32) % 7 == 0))
    is_tree = is_tree | orchard_grid
    is_tree[:4, :] = is_tree[-4:, :] = is_tree[:, :4] = is_tree[:, -4:] = False

    tree_bytes = np.zeros((h, w), dtype=np.uint8)
    tz_idx, tx_idx = np.where(is_tree)
    heights = np.clip(np.round(np.maximum(5.0, chm[tz_idx, tx_idx])), 4, 24).astype(np.uint8)
    prom = rel_prominence[tz_idx, tx_idx]
    conif = conif_mask[tz_idx, tx_idx]
    heath_t = heath_mask[tz_idx, tx_idx]
    orch = orchard_mask[tz_idx, tx_idx]
    thash = ((tx_idx.astype(np.int64) * 73428767 ^ tz_idx.astype(np.int64) * 912931) & 0x7FFFFFFF) % 100

    # Altitude-agnostic species classification using OSM leaf_type, landuse, canopy height, and local prominence
    species = np.ones_like(heights, dtype=np.uint8)
    species[conif | ((prom > 90.0) & (thash < 18))] = 3
    species[(~conif) & (((prom > 110.0) & (thash < 45)) | ((heights <= 9) & (thash < 35)))] = 2
    species[heath_t | (prom > 175.0) | (heights <= 5) | orch] = 4
    tree_bytes[tz_idx, tx_idx] = (species << 5) | (heights & 0x1F)

    nat_surf = np.zeros((h, w), dtype=np.uint8)
    nat_surf[meadow_mask] = 5
    nat_surf[farmland_mask] = 4
    nat_surf[park_mask] = 11
    nat_surf[((chm >= 4.0) | wood_mask) & (~bldg_buf_mask)] = 8
    nat_surf[heath_mask | ((rel_prominence > 180.0) & (~wood_mask) & (~farmland_mask))] = 1
    nat_surf[slope_mag > 0.45] = 3
    nat_surf[slope_mag > 0.62] = 2
    nat_surf[parking_mask] = 10
    nat_surf[water_mask] = 9

    road_surf = np.zeros((h, w), dtype=np.uint8)
    lidar_hedge = (chm >= 1.4) & (chm < 3.4) & (~bldg_buf_mask) & (~road_any_mask) & (~water_mask) & (slope_mag < 0.55)
    road_surf[lidar_hedge | (barrier_arr == 255)] = 10
    road_surf[barrier_arr == 200] = 9
    road_surf[barrier_arr == 150] = 11
    road_surf[paths_arr == 180] = 6
    road_surf[paths_arr == 255] = 7
    gate_mask = (paths_arr > 0) & (barrier_arr > 0)
    road_surf[gate_mask] = 12
    road_surf[paved_arr == 200] = 5
    road_surf[paved_arr == 255] = 4
    road_surf[rail_arr > 0] = 8
    road_surf[lamp_arr & (~road_any_mask) & (~(id_arr > 0))] = 13

    packed_surf = ((road_surf & 0x0F) << 4) | (nat_surf & 0x0F)

    with open(out_file, "wb") as f:
        f.write(struct.pack(">4siii", b"MLV4", TILE_SIZE, TILE_SIZE, int(datum_shift)))
        f.write(elev_packed.astype(">i2").tobytes())
        f.write(packed_surf.tobytes())
        f.write(tree_bytes.tobytes())
        f.write(bldg_packed.astype(">u2").tobytes())
        f.write(extras.tobytes())

    elev_min, elev_max = float(np.min(dtm)), float(np.max(dtm))
    summary = (
        f"DONE tile_({tx},{tz}) | {provider_name} | "
        f"elev={elev_min:.0f}..{elev_max:.0f}m, {len(bldg_meta):,} bldgs, {len(tz_idx):,} trees, {len(discovered_landmarks)} places"
    )
    if tracker:
        tracker.finish_tile(tx, tz, summary)
    return discovered_landmarks, summary


# ============================================================================
# 4. Config / Landmarks / Server Bootstrapper
# ============================================================================

def read_grid_config(config_path: str):
    origin_e, origin_n, datum_shift, crs = 366000, 252384, 104, "EPSG:27700"
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            txt = f.read()
        m_e = re.search(r"origin_easting:\s*(-?\d+)", txt)
        m_n = re.search(r"origin_northing:\s*(-?\d+)", txt)
        m_d = re.search(r"datum_shift:\s*(-?\d+)", txt)
        m_c = re.search(r"crs:\s*([^\s#]+)", txt)
        if m_e:
            origin_e = int(m_e.group(1))
        if m_n:
            origin_n = int(m_n.group(1))
        if m_d:
            datum_shift = int(m_d.group(1))
        if m_c:
            crs = m_c.group(1).strip("'\"")
    return origin_e, origin_n, datum_shift, crs


def write_grid_config(config_path: str, origin_e: int, origin_n: int, datum_shift: int, crs: str, default_spawn_key: str):
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    content = f"""# LiDARCraft 1:1 LiDAR + OpenStreetMap Map Configuration (MLV4)
grid:
  crs: "{crs}"
  origin_easting: {origin_e}
  origin_northing: {origin_n}
  datum_shift: {datum_shift}
  default_spawn: {default_spawn_key}

features:
  roads: true
  cliffs: true
  trees: true
  buildings: true
  hedges: true
  flora: true
  slabs: true
"""
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(content)


def bootstrap_paper_server(server_dir: str):
    """Ensure PaperMC jar, eula.txt, server.properties, start scripts, and LiDARCraft plugin jar are installed."""
    os.makedirs(server_dir, exist_ok=True)
    plugins_dir = os.path.join(server_dir, "plugins")
    os.makedirs(plugins_dir, exist_ok=True)

    paper_jar = os.path.join(server_dir, "paper.jar")
    if not os.path.exists(paper_jar):
        print("Downloading latest PaperMC server jar...", flush=True)
        proj = requests.get("https://api.papermc.io/v2/projects/paper", timeout=20).json()
        ver = proj["versions"][-1]
        builds = requests.get(f"https://api.papermc.io/v2/projects/paper/versions/{ver}/builds", timeout=20).json()
        b = builds["builds"][-1]
        b_num = b["build"]
        dl_name = b["downloads"]["application"]["name"]
        dl_url = f"https://api.papermc.io/v2/projects/paper/versions/{ver}/builds/{b_num}/downloads/{dl_name}"
        with requests.get(dl_url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(paper_jar, "wb") as f:
                shutil.copyfileobj(r.raw, f)
        print(f"Downloaded Paper {ver} (build {b_num}) -> {paper_jar}", flush=True)

    eula_file = os.path.join(server_dir, "eula.txt")
    if not os.path.exists(eula_file):
        with open(eula_file, "w", encoding="utf-8") as f:
            f.write("eula=true\n")

    props_file = os.path.join(server_dir, "server.properties")
    if not os.path.exists(props_file):
        with open(props_file, "w", encoding="utf-8") as f:
            f.write(
                "gamemode=creative\n"
                "force-gamemode=true\n"
                "difficulty=peaceful\n"
                "allow-flight=true\n"
                "spawn-protection=0\n"
                "view-distance=16\n"
                "simulation-distance=8\n"
                "motd=\\u00A76\\u00A7lLiDARCraft\\u00A7r \\u00A77- 1:1 Real-World LiDAR & OSM\n"
            )

    bat_file = os.path.join(server_dir, "start_server.bat")
    if not os.path.exists(bat_file):
        with open(bat_file, "w", encoding="utf-8") as f:
            f.write("@echo off\njava -Xms3G -Xmx5G -jar paper.jar --nogui\npause\n")

    sh_file = os.path.join(server_dir, "start_server.sh")
    if not os.path.exists(sh_file):
        with open(sh_file, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env bash\njava -Xms3G -Xmx5G -jar paper.jar --nogui\n")

    target_jar = os.path.join(plugins_dir, "LiDARCraft-1.0.0.jar")
    if not os.path.exists(target_jar):
        plugin_src_dir = os.path.join(SCRIPT_DIR, "plugin")
        built_jar = os.path.join(plugin_src_dir, "target", "LiDARCraft-1.0.0.jar")
        if not os.path.exists(built_jar) and os.path.exists(os.path.join(plugin_src_dir, "pom.xml")):
            print("Building LiDARCraft plugin with Maven...", flush=True)
            import subprocess
            subprocess.run(["mvn", "clean", "package", "-q"], cwd=plugin_src_dir, shell=(os.name == "nt"), check=True)
        if os.path.exists(built_jar):
            shutil.copy2(built_jar, target_jar)
            print(f"Installed plugin -> {target_jar}", flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="LiDARCraft — Generate a 1:1 scale LiDAR + OpenStreetMap Minecraft map from a Google Maps URL, Postcode, Lat/Lon, or Address."
    )
    parser.add_argument(
        "location",
        help="Google Maps URL, UK/Global Postcode (e.g. 'BA6 8YH'), 'lat,lon', or any street address/landmark",
    )
    parser.add_argument(
        "--size",
        type=float,
        default=None,
        help="Side length of the area to generate in kilometres (default: 4km = 2x2 tiles = 16.8 km^2)",
    )
    parser.add_argument(
        "--server-dir",
        default=DEFAULT_SERVER_DIR,
        help="Path to the PaperMC server directory",
    )
    parser.add_argument(
        "--new-map",
        action="store_true",
        help="Re-center the map origin, CRS, and elevation datum on this location (clears previous tiles)",
    )
    parser.add_argument(
        "--spawn",
        action="store_true",
        help="Set this location as the default server spawn point (even without --new-map)",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Start the PaperMC server immediately after building the map",
    )
    parser.add_argument(
        "--dtm",
        default=None,
        help="Optional path to custom local DTM GeoTIFF file",
    )
    parser.add_argument(
        "--dsm",
        default=None,
        help="Optional path to custom local DSM GeoTIFF file",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel tile worker threads (default: 4)",
    )
    args = parser.parse_args()

    bootstrap_paper_server(args.server_dir)

    plugin_dir = os.path.join(args.server_dir, "plugins", "LiDARCraft")
    legacy_dir = os.path.join(args.server_dir, "plugins", "MalvernHills")
    if not os.path.exists(plugin_dir) and os.path.exists(legacy_dir):
        plugin_dir = legacy_dir
    tiles_dir = os.path.join(plugin_dir, "tiles")
    config_path = os.path.join(plugin_dir, "config.yml")
    lm_path = os.path.join(plugin_dir, "landmarks.json")

    existing_crs = None
    if not args.new_map and os.path.exists(config_path):
        origin_e, origin_n, datum_shift, existing_crs = read_grid_config(config_path)

    loc = resolve_location(args.location, force_crs=existing_crs)
    size_km = args.size if args.size is not None else loc["suggested_size_km"]
    n_tiles_side = max(1, int(math.ceil((size_km * 1000.0) / TILE_SIZE)))

    if args.new_map or not os.path.exists(config_path):
        crs_str = loc["crs"]
        half_span = (n_tiles_side * TILE_SIZE) // 2
        origin_e = ((loc["easting"] - half_span) // 1000) * 1000
        origin_n = ((loc["northing"] + half_span + 999) // 1000) * 1000
        probe_dtm, _, _, _ = fetch_elevation_pair(
            origin_e, origin_e + TILE_SIZE, origin_n - TILE_SIZE, origin_n,
            crs_str, loc["country_code"], args.dtm, args.dsm
        )
        datum_shift = int(math.floor(float(np.min(probe_dtm)))) - 32
        if args.new_map and os.path.exists(tiles_dir):
            shutil.rmtree(tiles_dir, ignore_errors=True)
        write_grid_config(config_path, origin_e, origin_n, datum_shift, crs_str, loc["key"])
    else:
        crs_str = existing_crs
        center_e0 = origin_e + ((loc["easting"] - origin_e) // TILE_SIZE) * TILE_SIZE
        center_n1 = origin_n - ((origin_n - loc["northing"]) // TILE_SIZE) * TILE_SIZE
        probe_dtm, _, _, _ = fetch_elevation_pair(
            center_e0, center_e0 + TILE_SIZE, center_n1 - TILE_SIZE, center_n1,
            crs_str, loc["country_code"], args.dtm, args.dsm
        )
        p_min, p_max = float(np.min(probe_dtm)), float(np.max(probe_dtm))
        if (p_min - datum_shift) < -48 or (p_max - datum_shift) > 295:
            datum_shift = int(math.floor(p_min)) - 32

    rel_x = loc["easting"] - origin_e
    rel_z = origin_n - loc["northing"]
    center_tx = rel_x // TILE_SIZE
    center_tz = rel_z // TILE_SIZE

    if n_tiles_side == 1:
        tx_list = [center_tx]
        tz_list = [center_tz]
    else:
        frac_x = (rel_x - center_tx * TILE_SIZE) / float(TILE_SIZE)
        frac_z = (rel_z - center_tz * TILE_SIZE) / float(TILE_SIZE)
        start_tx = center_tx - (n_tiles_side // 2) + (1 if (n_tiles_side % 2 == 0 and frac_x >= 0.5) else 0)
        start_tz = center_tz - (n_tiles_side // 2) + (1 if (n_tiles_side % 2 == 0 and frac_z >= 0.5) else 0)
        tx_list = [start_tx + i for i in range(n_tiles_side)]
        tz_list = [start_tz + i for i in range(n_tiles_side)]

    tiles_to_build = [(tx, tz) for tz in tz_list for tx in tx_list]
    area_km2 = len(tiles_to_build) * (TILE_SIZE / 1000.0) ** 2
    print(f"Location : {loc['label']} ({loc['lat']:.5f}, {loc['lon']:.5f}) [{crs_str}]", flush=True)
    print(f"Area     : {n_tiles_side}x{n_tiles_side} tiles ({area_km2:.1f} km^2) | datum={datum_shift}m", flush=True)

    existing_landmarks = {}
    if not args.new_map and os.path.exists(lm_path):
        try:
            with open(lm_path, "r", encoding="utf-8") as f:
                for item in json.load(f):
                    existing_landmarks[item["key"]] = item
        except Exception:
            pass

    existing_landmarks[loc["key"]] = {
        "key": loc["key"],
        "label": loc["label"],
        "easting": loc["easting"],
        "northing": loc["northing"],
    }

    tracker = ProgressTracker(len(tiles_to_build))
    with ThreadPoolExecutor(max_workers=min(args.workers, len(tiles_to_build))) as pool:
        futs = {
            pool.submit(
                build_tile, tx, tz, origin_e, origin_n, datum_shift, tiles_dir,
                crs_str, loc["country_code"], args.dtm, args.dsm, tracker
            ): (tx, tz)
            for tx, tz in tiles_to_build
        }
        for fut in as_completed(futs):
            lms, _ = fut.result()
            existing_landmarks.update(lms)

    os.makedirs(os.path.dirname(lm_path), exist_ok=True)
    with open(lm_path, "w", encoding="utf-8") as f:
        json.dump(list(existing_landmarks.values()), f, indent=2)

    if args.spawn and not args.new_map and os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg_txt = f.read()
        cfg_txt = re.sub(r"default_spawn:\s*\S+", f"default_spawn: {loc['key']}", cfg_txt)
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(cfg_txt)

    mc_x = loc["easting"] - origin_e
    mc_z = origin_n - loc["northing"]
    print(f"\nDone! In-game: /lc {loc['key']}  (Minecraft X={mc_x}, Z={mc_z})", flush=True)

    if args.run:
        import subprocess
        print(f"Starting PaperMC server in {args.server_dir} (connect to localhost:25565)...", flush=True)
        subprocess.run(["java", "-Xms3G", "-Xmx5G", "-jar", "paper.jar", "--nogui"], cwd=args.server_dir)


if __name__ == "__main__":
    main()
