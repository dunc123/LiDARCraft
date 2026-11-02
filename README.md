# LiDARCraft

Generate a **1:1 scale (`1 block = 1 metre`) Minecraft world** from real-world 1m LiDAR elevation models (`DTM` + `DSM`) and OpenStreetMap (`OSM`) vectors.

---

## Quick Start

1. Click the green **`<> Code`** button at the top of this page -> **`Download ZIP`**, and extract the ZIP folder.
2. Run **`start.bat`** (Windows) or **`./start.sh`** (macOS / Linux).
3. Enter any place name, postcode, or Google Maps URL when prompted (e.g. `Glastonbury`, `Three Cliffs Bay, Wales`, or `Kings Cross, London`).
4. Open **Minecraft Java Edition** -> **Multiplayer** -> Connect to **`localhost`**.

---

## In-Game Commands (`/lc` or `/lidar`)

- **`/lc`** — Open the interactive menu with clickable `[layer]` toggles and nearest landmarks.
- **`/lc <place | postcode | address | lat,lon>`** — Teleport to a landmark or generate a new real-world location on the fly.
- **`/lc places [search]`** — List and filter saved landmarks sorted by distance.
- **`/lc toggle <roads|cliffs|trees|buildings|hedges|flora|slabs>`** — Toggle a feature layer on or off.
- **`/lc reload`** — Reload landmarks and newly compiled tiles.
- **`/speed <1-10>`** — Set Creative flight and walk speed.

---

## CLI Usage

```bash
python build_map.py <location> [options]
```

| Flag | Default | Description |
| :--- | :--- | :--- |
| `location` | *(required)* | Place name (`"Glastonbury"`), postcode (`"BA6 8YH"`), Google Maps URL, or `"lat,lon"`. |
| `--size <km>` | `4.0` | Side length of the area in km (rounded up to $2,048\text{m}$ tiles). |
| `--new-map` | `False` | Re-center the map origin, CRS, and vertical datum on this location (clears previous tiles). |
| `--spawn` | `False` | Set this location as the default server spawn point. |
| `--run` | `False` | Start the PaperMC server immediately after building. |
| `--server-dir <dir>` | `../local_server` | Target PaperMC server directory. |
| `--dtm <file.tif>` | `None` | Custom local bare-earth DTM GeoTIFF. |
| `--dsm <file.tif>` | `None` | Custom local canopy/surface DSM GeoTIFF. |
| `--workers <n>` | `4` | Parallel worker threads. |

---

## Features & Data Sources

- **Terrain & Coastlines:** Sub-metre `0.5m` slab smoothing, cliffs, scree, rivers, beaches, sand dunes, coastal shingle, and sea level (`0m AOD`).
- **3D Buildings & Infrastructure:** OSM building footprints extruded to LiDAR roof heights with multi-storey windows, doorways, pitched roof slabs, chimneys, bridges, railways, roads, streetlamps, footpaths, walls, and gates.
- **Vegetation:** Canopy-detected individual trees (`4m–24m`), hedgerows, orchards, farmland furrows, wildflower meadows, and heathland.
- **Elevation Providers:**
  - **England:** UK Environment Agency 1m Composite LiDAR (`DTM` + `First-Return DSM`).
  - **Wales:** Natural Resources Wales (DataMapWales) 1m LiDAR (`DTM` + `DSM` Cloud-Optimized GeoTIFFs).
  - **United States:** USGS 3DEP Elevation + OSM 3D synthesis.
  - **Worldwide:** AWS Terrain Tiles (`Terrarium`) + OSM 3D synthesis, projected via `pyproj` into local metric UTM zones (`EPSG:326xx` / `327xx`).
