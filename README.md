# LiDARCraft — 1:1 Real-World LiDAR + OpenStreetMap Minecraft Generator (`MLV4`)

Generate a **1:1 scale (`1 block = 1 metre`) Minecraft world** from real-world 1m LiDAR elevation models (`DTM` + `First-Return DSM`) and OpenStreetMap (`OSM`) vectors—simply by passing a **Google Maps link, Postcode, `lat,lon` coordinate pair, or street address**.

---

## Features

- **Sub-Metre `0.5m` Terrain & Roof Smoothing (`Slabs`):** Uses float32 LiDAR fractional elevations (`>= 0.5m`) to place top slabs across ridges, roads, paths, and pitched roofs.
- **3D LiDAR + OSM Buildings:**
  - True building footprints from OpenStreetMap combined with 1m LiDAR canopy/surface heights (`DSM - DTM`) and `building:levels`.
  - Algorithmic multi-storey window grids (`Glass Panes`), corner pillars, road-facing doorways (`Spruce Door`), pitched roof slabs, and 1m LiDAR roof chimney spike detection (`Bricks` + `Flower Pot`).
- **True 3D Bridges (`bridge=yes`):** Elevated `Stone Brick` decks with `Cobblestone Wall` parapets spanning over rivers, streams, and railway cuttings.
- **Canopy-Detected Trees & Hedgerows:** Individual tree trunks and crowns (`4m–24m` tall) extracted via local maxima of the 1m Canopy Height Model (`CHM = DSM - DTM`), plus hedgerows (`1.4m–3.4m`), dry-stone walls, wooden fences, gates where footpaths cross barriers, streetlamps, railways, farmland furrows, wildflower meadows, and high-prominence heath/bracken.
- **Unified Nominatim + `pyproj` Geolocation & Multi-Country LiDAR:**
  - **Coordinate Systems:** Uses `pyproj` to select `EPSG:27700` (British National Grid) in Great Britain and the authoritative local WGS84 UTM zone (`EPSG:326xx` / `EPSG:327xx`) anywhere else on Earth.
  - **England (Tier 1):** UK Environment Agency 1m Composite LiDAR (`1m DTM` + `1m First-Return DSM` WCS) — zero API keys required.
  - **United States (Tier 2):** USGS National Map 3DEP (`1m LiDAR` / `1/3-arcsec` float32 GeoTIFF REST API) + OSM 3D synthesis.
  - **Worldwide (Tier 3):** AWS Open Data Terrain Tiles (`Terrarium z=15`, combining open national LiDAR datasets in Europe/Alps/Norway/NZ with Copernicus DEM at sub-metre vertical resolution) + OSM 3D woodland/building synthesis.
  - **Custom Local GeoTIFF:** Pass `--dtm my_dtm.tif --dsm my_dsm.tif` for local national LiDAR files.

---

## Setup & Quick Start

You can either let `build_map.py` bootstrap everything in **one command**, or follow the **step-by-step manual setup**.

### Prerequisites
- **Python 3.10+**
- **Java 21+** (for PaperMC 1.21+)
- **Maven** (`mvn`, only needed if compiling the Java plugin from source)

---

### Option A: Automatic One-Command Setup
`build_map.py` automatically downloads the latest **PaperMC** server jar, accepts `eula.txt`, writes `server.properties` (Creative mode + flight enabled), builds/installs `LiDARCraft-1.0.0.jar`, generates `start_server.bat` / `start_server.sh`, compiles your 1:1 LiDAR + OSM tiles, and (with `--run`) starts the server:

```bash
pip install -r requirements.txt
python build_map.py "Glastonbury" --new-map --run
```
Then open Minecraft Java Edition -> **Multiplayer** -> Connect to **`localhost`**.

---

### Option B: Step-by-Step Setup

1. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
2. **Build the PaperMC plugin (optional if already compiled):**
   ```bash
   cd plugin
   mvn clean package
   cd ..
   ```
3. **Generate your first map (also bootstraps `paper.jar` & `eula.txt` in `../local_server`):**
   ```bash
   # Pass any place name, postcode, Google Maps URL, or "lat,lon":
   python build_map.py "Glastonbury" --new-map
   ```
4. **Add more regions onto the same server (optional):**
   ```bash
   python build_map.py "BA6 8YH" --size 4
   python build_map.py "https://www.google.com/maps/@52.0981,-2.3378,14z" --size 6
   ```
5. **Start the Minecraft Server:**
   - **Windows:** Run `..\local_server\start_server.bat` (or `python build_map.py "Glastonbury" --run`)
   - **Linux / macOS:** Run `../local_server/start_server.sh`
   - Connect in Minecraft Java Edition to **`localhost`** (`localhost:25565`).

---

## CLI Options (`python build_map.py --help`)

| Flag | Default | Description |
| :--- | :--- | :--- |
| `location` | *(required)* | Place name (`"Glastonbury"`), postcode (`"BA6 8YH"`), Google Maps URL, or `"lat,lon"`. |
| `--size <km>` | `4.0` | Side length of the square area in km (rounded up to $2,048\text{m}$ tiles). |
| `--new-map` | `False` | Re-center the map origin, `pyproj` CRS, and vertical datum on this location (clears previous tiles). |
| `--spawn` | `False` | Set this location as the default server spawn point without wiping existing tiles. |
| `--run` | `False` | Launch the PaperMC server immediately after the tiles finish building. |
| `--server-dir <dir>` | `../local_server` | Path to the PaperMC server folder. |
| `--dtm <file.tif>` | `None` | Optional path to a custom local bare-earth DTM GeoTIFF. |
| `--dsm <file.tif>` | `None` | Optional path to a custom local canopy/surface DSM GeoTIFF. |
| `--workers <n>` | `4` | Number of parallel tile worker threads. |

---

## In-Game Commands (`/lc` or `/lidar`)

Everything in-game runs through a single command with **tab-completion** and **clickable chat pills**:

- `/lc` — Open the interactive status menu: click any `[layer]` pill (`roads`, `cliffs`, `trees`, `buildings`, `hedges`, `flora`, `slabs`) to toggle it live, or click any nearby landmark pill to teleport straight there.
- `/lc <place | postcode | address | lat,lon>` — Teleport to any landmark or real-world location (e.g. `/lc glastonburytor`, `/lc BA6 8YH`, `/lc Cheddar Gorge`). If that tile isn't built yet, the server builds it in the background with live progress in chat and teleports you automatically when ready!
- `/lc <layer>` — Toggle a feature layer on or off (e.g. `/lc trees`, `/lc buildings`).
- `/lc reload` — Reload `landmarks.json` and newly compiled `.bin` tiles without restarting.
- `/speed <1-10>` — Adjust Creative fly/walk speed multiplier.
