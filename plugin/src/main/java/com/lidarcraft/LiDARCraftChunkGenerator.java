package com.lidarcraft;

import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.block.Biome;
import org.bukkit.generator.BiomeProvider;
import org.bukkit.generator.BlockPopulator;
import org.bukkit.generator.ChunkGenerator;
import org.bukkit.generator.WorldInfo;

import java.io.BufferedInputStream;
import java.io.DataInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.concurrent.ConcurrentHashMap;

public class LiDARCraftChunkGenerator extends ChunkGenerator {

    public static final int TILE_SHIFT = 11;
    public static final int TILE_SIZE = 2048;
    public static final int TILE_MASK = TILE_SIZE - 1;

    private static final class TileData {
        final int tileDatumShift;
        final short[] elevations; // (topY << 1) | halfStep
        final byte[] surfaces;    // (overlay << 4) | natural
        final byte[] trees;       // (species << 5) | height_m
        final short[] buildings;  // (wallType << 12) | (eaveH << 6) | roofH
        final byte[] extras;      // building style/slab/chimney OR bridge deck offset

        TileData(int tileDatumShift, short[] elevations, byte[] surfaces, byte[] trees, short[] buildings, byte[] extras) {
            this.tileDatumShift = tileDatumShift;
            this.elevations = elevations;
            this.surfaces = surfaces;
            this.trees = trees;
            this.buildings = buildings;
            this.extras = extras;
        }
    }

    private final File tilesDir;
    private final int datumShift;
    private final Map<Long, TileData> tileCache = new ConcurrentHashMap<>(16);

    private volatile boolean roadsEnabled = true;
    private volatile boolean cliffsEnabled = true;
    private volatile boolean treesEnabled = true;
    private volatile boolean buildingsEnabled = true;
    private volatile boolean hedgesEnabled = true;
    private volatile boolean floraEnabled = true;
    private volatile boolean slabsEnabled = true;

    private final BiomeProvider biomeProvider = new BiomeProvider() {
        @Override
        public Biome getBiome(WorldInfo worldInfo, int x, int y, int z) {
            return Biome.WINDSWEPT_FOREST;
        }

        @Override
        public List<Biome> getBiomes(WorldInfo worldInfo) {
            return Collections.singletonList(Biome.WINDSWEPT_FOREST);
        }
    };

    public LiDARCraftChunkGenerator(File tilesDir, int datumShift) {
        this.tilesDir = tilesDir;
        this.datumShift = datumShift;
    }

    public int getDatumShift() { return datumShift; }

    public int getDatumShiftAt(int x, int z) {
        TileData t = getTile(x >> TILE_SHIFT, z >> TILE_SHIFT);
        return (t != null) ? t.tileDatumShift : datumShift;
    }
    public boolean isRoadsEnabled() { return roadsEnabled; }
    public void setRoadsEnabled(boolean v) { this.roadsEnabled = v; }
    public boolean isCliffsEnabled() { return cliffsEnabled; }
    public void setCliffsEnabled(boolean v) { this.cliffsEnabled = v; }
    public boolean isTreesEnabled() { return treesEnabled; }
    public void setTreesEnabled(boolean v) { this.treesEnabled = v; }
    public boolean isBuildingsEnabled() { return buildingsEnabled; }
    public void setBuildingsEnabled(boolean v) { this.buildingsEnabled = v; }
    public boolean isHedgesEnabled() { return hedgesEnabled; }
    public void setHedgesEnabled(boolean v) { this.hedgesEnabled = v; }
    public boolean isFloraEnabled() { return floraEnabled; }
    public void setFloraEnabled(boolean v) { this.floraEnabled = v; }
    public boolean isSlabsEnabled() { return slabsEnabled; }
    public void setSlabsEnabled(boolean v) { this.slabsEnabled = v; }

    public void clearTileCache() {
        tileCache.clear();
    }

    public boolean hasTileAtBlock(int x, int z) {
        int tx = x >> TILE_SHIFT;
        int tz = z >> TILE_SHIFT;
        File f = new File(tilesDir, "tile_" + tx + "_" + tz + ".bin");
        return f.exists() && f.length() >= 25_000_000L;
    }

    private TileData getTile(int tx, int tz) {
        if (tx < -10000 || tz < -10000 || tx > 10000 || tz > 10000) return null;
        long key = (((long) tx) << 32) | (tz & 0xFFFFFFFFL);
        TileData cached = tileCache.get(key);
        if (cached != null) return cached;

        File f = new File(tilesDir, "tile_" + tx + "_" + tz + ".bin");
        if (!f.exists() || f.length() < 25_000_000L) return null;

        synchronized (this) {
            cached = tileCache.get(key);
            if (cached != null) return cached;
            try (DataInputStream in = new DataInputStream(new BufferedInputStream(new FileInputStream(f), 1 << 20))) {
                byte[] magic = new byte[4];
                in.readFully(magic);
                String m = new String(magic);
                int w = in.readInt();
                int h = in.readInt();
                int tileDatum = in.readInt();
                int total = w * h;
                short[] elev = readShortArray(in, total);
                byte[] surf = new byte[total];
                in.readFully(surf);
                byte[] tr = new byte[total];
                in.readFully(tr);
                short[] bldg = readShortArray(in, total);
                byte[] ext = new byte[total];
                if ("MLV4".equals(m)) {
                    in.readFully(ext);
                } else {
                    for (int i = 0; i < total; i++) {
                        elev[i] = (short) (elev[i] << 1);
                    }
                }
                TileData loaded = new TileData(tileDatum, elev, surf, tr, bldg, ext);
                if (tileCache.size() >= 14) {
                    tileCache.keySet().stream().findFirst().ifPresent(tileCache::remove);
                }
                tileCache.put(key, loaded);
                return loaded;
            } catch (IOException e) {
                return null;
            }
        }
    }

    private static short[] readShortArray(DataInputStream in, int total) throws IOException {
        short[] arr = new short[total];
        byte[] buf = new byte[total * 2];
        in.readFully(buf);
        for (int i = 0, b = 0; i < total; i++, b += 2) {
            arr[i] = (short) (((buf[b] & 0xFF) << 8) | (buf[b + 1] & 0xFF));
        }
        return arr;
    }

    public int getSurfaceY(int x, int z) {
        TileData t = getTile(x >> TILE_SHIFT, z >> TILE_SHIFT);
        if (t == null) return 64;
        int idx = ((z & TILE_MASK) << TILE_SHIFT) + (x & TILE_MASK);
        return t.elevations[idx] >> 1;
    }

    @Override
    public BiomeProvider getDefaultBiomeProvider(WorldInfo worldInfo) {
        return biomeProvider;
    }

    @Override
    public void generateNoise(WorldInfo worldInfo, Random random, int chunkX, int chunkZ, ChunkData chunkData) {
        int baseX = chunkX << 4;
        int baseZ = chunkZ << 4;

        TileData tile = getTile(baseX >> TILE_SHIFT, baseZ >> TILE_SHIFT);
        if (tile == null) return;

        int minY = worldInfo.getMinHeight();
        int maxY = worldInfo.getMaxHeight() - 1;

        for (int lx = 0; lx < 16; lx++) {
            int wx = baseX + lx;
            int txLocal = wx & TILE_MASK;

            for (int lz = 0; lz < 16; lz++) {
                int wz = baseZ + lz;
                int tzLocal = wz & TILE_MASK;
                int idx = (tzLocal << TILE_SHIFT) + txLocal;

                short rawElev = tile.elevations[idx];
                int topY = rawElev >> 1;
                boolean halfStep = (rawElev & 1) != 0;

                int packedSurf = tile.surfaces[idx] & 0xFF;
                int roadCode = (packedSurf >> 4) & 0x0F;
                int natCode = packedSurf & 0x0F;
                int bldg = tile.buildings[idx] & 0xFFFF;
                int ext = tile.extras[idx] & 0xFF;

                int surf = resolveSurfaceCode(natCode, roadCode);
                generateTerrainColumn(chunkData, lx, lz, wx, wz, minY, topY, maxY, surf, halfStep && (bldg == 0));

                if (roadsEnabled && (ext & 0x80) != 0) {
                    int bridgeY = Math.min(maxY - 2, topY + (ext & 0x3F));
                    boolean isParapet = (ext & 0x40) != 0;
                    chunkData.setBlock(lx, bridgeY - 1, lz, Material.STONE_BRICKS);
                    chunkData.setBlock(lx, bridgeY, lz, isParapet ? Material.STONE_BRICKS : Material.CYAN_TERRACOTTA);
                    if (isParapet) {
                        chunkData.setBlock(lx, bridgeY + 1, lz, Material.COBBLESTONE_WALL);
                    }
                } else if (buildingsEnabled && bldg != 0) {
                    generateBuildingColumn(chunkData, lx, lz, wx, wz, topY, maxY, bldg, ext);
                }
            }
        }

        if (treesEnabled) {
            generateChunkTrees(chunkData, tile, baseX, baseZ, maxY);
        }
    }

    private int resolveSurfaceCode(int natCode, int roadCode) {
        int surf = natCode;
        if (roadCode >= 4 && roadCode <= 7 && roadsEnabled) surf = roadCode + 10; // 14..17
        else if (roadCode == 8 && roadsEnabled) surf = 18; // Railway
        else if (roadCode == 13 && roadsEnabled) surf = 19; // Streetlamp
        else if (roadCode >= 9 && roadCode <= 12 && hedgesEnabled) surf = roadCode + 11; // 20..23
        if (!cliffsEnabled && (surf == 2 || surf == 3)) surf = 0;
        if (!treesEnabled && surf == 8) surf = 0;
        if (!floraEnabled && (surf == 4 || surf == 5)) surf = 0;
        return surf;
    }

    private void generateTerrainColumn(ChunkData chunkData, int lx, int lz, int wx, int wz, int minY, int topY, int maxY, int surf, boolean halfStep) {
        chunkData.setBlock(lx, minY, lz, Material.BEDROCK);

        int deepslateTop = Math.min(0, topY - 4);
        if (deepslateTop > minY) {
            chunkData.setRegion(lx, minY + 1, lz, lx + 1, deepslateTop + 1, lz + 1, Material.DEEPSLATE);
        }

        int stoneBottom = Math.max(minY + 1, deepslateTop + 1);
        int stoneTop = topY - 3;
        if (stoneTop >= stoneBottom) {
            chunkData.setRegion(lx, stoneBottom, lz, lx + 1, stoneTop + 1, lz + 1, Material.STONE);
        }

        int hMod = (((wx * 73428767) ^ (wz * 912931) ^ (topY * 31337)) & 0x7FFFFFFF) % 100;
        int subBottom = Math.max(minY + 1, topY - 2);
        boolean canPlaceAbove = topY + 1 < maxY;

        switch (surf) {
            case 2: { // Cliff rock
                Material rock = (hMod < 45) ? Material.STONE : (hMod < 75) ? Material.ANDESITE : (hMod < 90) ? Material.TUFF : Material.GRANITE;
                chunkData.setRegion(lx, Math.max(minY + 1, topY - 3), lz, lx + 1, topY + 1, lz + 1, rock);
                if (slabsEnabled && halfStep && canPlaceAbove) chunkData.setBlock(lx, topY + 1, lz, Material.ANDESITE_SLAB);
                break;
            }
            case 3: { // Scree
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY, lz + 1, Material.STONE);
                Material scree = (hMod < 35) ? Material.ANDESITE : (hMod < 65) ? Material.COBBLESTONE : (hMod < 85) ? Material.COARSE_DIRT : Material.MOSS_BLOCK;
                chunkData.setBlock(lx, topY, lz, scree);
                if (slabsEnabled && halfStep && canPlaceAbove && hMod < 65) chunkData.setBlock(lx, topY + 1, lz, Material.COBBLESTONE_SLAB);
                break;
            }
            case 4: { // Farmland
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY, lz + 1, Material.DIRT);
                boolean cropRow = ((wx + wz) % 3) != 0;
                chunkData.setBlock(lx, topY, lz, cropRow ? Material.COARSE_DIRT : Material.MOSS_BLOCK);
                if (floraEnabled && canPlaceAbove && cropRow && hMod < 35) {
                    chunkData.setBlock(lx, topY + 1, lz, Material.SHORT_GRASS);
                }
                break;
            }
            case 5: { // Meadow
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY, lz + 1, Material.DIRT);
                chunkData.setBlock(lx, topY, lz, Material.GRASS_BLOCK);
                if (floraEnabled && canPlaceAbove && hMod < 28) {
                    Material fl = (hMod < 20) ? Material.SHORT_GRASS : (hMod < 24) ? Material.DANDELION : (hMod < 26) ? Material.OXEYE_DAISY : Material.CORNFLOWER;
                    chunkData.setBlock(lx, topY + 1, lz, fl);
                }
                break;
            }
            case 8: { // Forest floor
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY, lz + 1, Material.DIRT);
                Material floor = (hMod < 55) ? Material.GRASS_BLOCK : (hMod < 80) ? Material.PODZOL : (hMod < 92) ? Material.MOSS_BLOCK : Material.COARSE_DIRT;
                chunkData.setBlock(lx, topY, lz, floor);
                if (floraEnabled && canPlaceAbove && hMod < 14) chunkData.setBlock(lx, topY + 1, lz, Material.FERN);
                break;
            }
            case 9: // Water
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY, lz + 1, Material.CLAY);
                chunkData.setBlock(lx, topY, lz, Material.WATER);
                break;
            case 10: // Car park
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY, lz + 1, Material.STONE);
                chunkData.setBlock(lx, topY, lz, (hMod < 50) ? Material.GRAVEL : Material.ANDESITE);
                break;
            case 11: // Park / Pitch
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY, lz + 1, Material.DIRT);
                chunkData.setBlock(lx, topY, lz, (hMod < 70) ? Material.MOSS_BLOCK : Material.GRASS_BLOCK);
                break;
            case 14: // Major road
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY, lz + 1, Material.STONE_BRICKS);
                chunkData.setBlock(lx, topY, lz, Material.CYAN_TERRACOTTA);
                if (slabsEnabled && halfStep && canPlaceAbove) chunkData.setBlock(lx, topY + 1, lz, Material.STONE_BRICK_SLAB);
                break;
            case 15: // Minor road
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY, lz + 1, Material.STONE);
                chunkData.setBlock(lx, topY, lz, (hMod < 60) ? Material.ANDESITE : Material.COBBLESTONE);
                if (slabsEnabled && halfStep && canPlaceAbove) chunkData.setBlock(lx, topY + 1, lz, Material.ANDESITE_SLAB);
                break;
            case 16: // Track / Bridleway
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY, lz + 1, Material.DIRT);
                chunkData.setBlock(lx, topY, lz, (hMod < 65) ? Material.COARSE_DIRT : Material.PACKED_MUD);
                if (slabsEnabled && halfStep && canPlaceAbove) chunkData.setBlock(lx, topY + 1, lz, Material.MUD_BRICK_SLAB);
                break;
            case 17: // Footpath (0.5m slab steps for no-jump walking!)
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY, lz + 1, Material.DIRT);
                if (slabsEnabled && halfStep && canPlaceAbove) {
                    chunkData.setBlock(lx, topY, lz, Material.COARSE_DIRT);
                    chunkData.setBlock(lx, topY + 1, lz, Material.COBBLESTONE_SLAB);
                } else {
                    chunkData.setBlock(lx, topY, lz, Material.DIRT_PATH);
                }
                break;
            case 18: // Railway
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY, lz + 1, Material.GRAVEL);
                chunkData.setBlock(lx, topY, lz, ((wx + wz) % 2 == 0) ? Material.POLISHED_DEEPSLATE_SLAB : Material.GRAVEL);
                break;
            case 19: // Streetlamp post
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY + 1, lz + 1, Material.STONE_BRICKS);
                if (topY + 4 < maxY) {
                    chunkData.setBlock(lx, topY + 1, lz, Material.COBBLESTONE_WALL);
                    chunkData.setBlock(lx, topY + 2, lz, Material.IRON_BARS);
                    chunkData.setBlock(lx, topY + 3, lz, Material.IRON_BARS);
                    chunkData.setBlock(lx, topY + 4, lz, Material.LANTERN);
                }
                break;
            case 20: // Stone wall
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY + 1, lz + 1, Material.STONE_BRICKS);
                if (canPlaceAbove) chunkData.setBlock(lx, topY + 1, lz, Material.COBBLESTONE_WALL);
                break;
            case 21: { // LiDAR Hedgerow
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY, lz + 1, Material.DIRT);
                chunkData.setBlock(lx, topY, lz, Material.PODZOL);
                Material hedge = (hMod < 75) ? Material.OAK_LEAVES : Material.AZALEA_LEAVES;
                if (canPlaceAbove) chunkData.setBlock(lx, topY + 1, lz, hedge);
                if (hMod < 45 && topY + 2 < maxY) chunkData.setBlock(lx, topY + 2, lz, hedge);
                break;
            }
            case 22: // Wooden fence
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY, lz + 1, Material.DIRT);
                chunkData.setBlock(lx, topY, lz, Material.GRASS_BLOCK);
                if (canPlaceAbove) chunkData.setBlock(lx, topY + 1, lz, Material.OAK_FENCE);
                break;
            case 23: // Gate
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY, lz + 1, Material.DIRT);
                chunkData.setBlock(lx, topY, lz, Material.COBBLESTONE);
                if (canPlaceAbove) chunkData.setBlock(lx, topY + 1, lz, Material.OAK_FENCE_GATE);
                break;
            case 1: { // Moorland / Heath / Bracken & Gorse
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY, lz + 1, Material.DIRT);
                Material ridge = (hMod < 72) ? Material.GRASS_BLOCK : (hMod < 92) ? Material.MOSS_BLOCK : Material.COARSE_DIRT;
                chunkData.setBlock(lx, topY, lz, ridge);
                if (floraEnabled && canPlaceAbove && ridge != Material.COARSE_DIRT && hMod < 22) {
                    Material fl = (hMod < 12) ? Material.FERN : (hMod < 19) ? Material.SHORT_GRASS : Material.DANDELION;
                    chunkData.setBlock(lx, topY + 1, lz, fl);
                }
                break;
            }
            default:
                chunkData.setRegion(lx, subBottom, lz, lx + 1, topY, lz + 1, Material.DIRT);
                chunkData.setBlock(lx, topY, lz, Material.GRASS_BLOCK);
                if (floraEnabled && canPlaceAbove && hMod < 10) chunkData.setBlock(lx, topY + 1, lz, Material.SHORT_GRASS);
                break;
        }
    }

    private void generateBuildingColumn(ChunkData chunkData, int lx, int lz, int wx, int wz, int topY, int maxY, int bldg, int ext) {
        int roofH = bldg & 0x3F;
        int eaveH = (bldg >> 6) & 0x3F;
        int wallType = (bldg >> 12) & 0x03; // 0=Interior, 1=Corner Pillar, 2=Window Wall, 3=Doorway

        int style = ext & 0x07;
        boolean roofHalf = (ext & 0x08) != 0;
        boolean isChimney = (ext & 0x10) != 0;

        int eaveY = Math.min(maxY - 1, topY + eaveH);
        int roofY = Math.min(maxY - 2, topY + roofH);
        if (roofY <= topY) return;

        Material wallMat, roofMat, roofSlab;
        switch (style) {
            case 1:
                wallMat = Material.CALCITE;
                roofMat = Material.DEEPSLATE_BRICKS;
                roofSlab = Material.DEEPSLATE_BRICK_SLAB;
                break;
            case 2:
                wallMat = Material.BRICKS;
                roofMat = Material.DEEPSLATE_TILES;
                roofSlab = Material.DEEPSLATE_TILE_SLAB;
                break;
            case 4:
                wallMat = Material.SMOOTH_SANDSTONE;
                roofMat = Material.DEEPSLATE_TILES;
                roofSlab = Material.DEEPSLATE_TILE_SLAB;
                break;
            case 5:
                wallMat = Material.POLISHED_ANDESITE;
                roofMat = Material.STONE_BRICKS;
                roofSlab = Material.STONE_BRICK_SLAB;
                break;
            default:
                wallMat = Material.STONE_BRICKS;
                roofMat = Material.COBBLESTONE;
                roofSlab = Material.COBBLESTONE_SLAB;
                break;
        }

        chunkData.setBlock(lx, topY, lz, Material.STONE_BRICKS);

        if (wallType != 0) {
            for (int y = topY + 1; y < roofY; y++) {
                int relFromEave = eaveY - y;
                if (wallType == 3 && (y == topY + 1 || y == topY + 2)) {
                    chunkData.setBlock(lx, y, lz, Material.AIR); // Doorway opening
                } else if (wallType == 2 && relFromEave >= 1 && (relFromEave % 4 == 2 || relFromEave % 4 == 3) && ((wx + wz) % 3 == 0)) {
                    chunkData.setBlock(lx, y, lz, Material.GLASS_PANE); // Multi-storey window grid
                } else {
                    chunkData.setBlock(lx, y, lz, wallMat);
                }
            }
            chunkData.setBlock(lx, roofY, lz, roofMat);
            if (slabsEnabled && roofHalf) {
                chunkData.setBlock(lx, roofY + 1, lz, roofSlab);
            }
        } else {
            int roofShellBottom = Math.max(topY + 1, roofY - 1);
            chunkData.setRegion(lx, roofShellBottom, lz, lx + 1, roofY + 1, lz + 1, roofMat);
            if (isChimney && roofY + 2 < maxY) {
                chunkData.setBlock(lx, roofY + 1, lz, Material.BRICKS);
                chunkData.setBlock(lx, roofY + 2, lz, Material.FLOWER_POT);
            } else if (slabsEnabled && roofHalf) {
                chunkData.setBlock(lx, roofY + 1, lz, roofSlab);
            }
        }
    }

    private void generateChunkTrees(ChunkData chunkData, TileData tile, int baseX, int baseZ, int maxY) {
        int tileBaseX = baseX & ~TILE_MASK;
        int tileBaseZ = baseZ & ~TILE_MASK;
        int localBaseX = baseX & TILE_MASK;
        int localBaseZ = baseZ & TILE_MASK;

        int zStart = Math.max(0, localBaseZ - 4);
        int zEnd = Math.min(TILE_SIZE - 1, localBaseZ + 19);
        int xStart = Math.max(0, localBaseX - 4);
        int xEnd = Math.min(TILE_SIZE - 1, localBaseX + 19);

        for (int lz = zStart; lz <= zEnd; lz++) {
            int rowOffset = lz << TILE_SHIFT;
            for (int lx = xStart; lx <= xEnd; lx++) {
                int tb = tile.trees[rowOffset + lx] & 0xFF;
                if (tb == 0) continue;

                int species = (tb >> 5) & 0x07;
                int treeH = tb & 0x1F;
                int groundY = tile.elevations[rowOffset + lx] >> 1;
                if (groundY + treeH >= maxY) continue;

                placeLidarTree(chunkData, baseX, baseZ, tileBaseX + lx, groundY, tileBaseZ + lz, species, treeH);
            }
        }
    }

    private void placeLidarTree(ChunkData chunkData, int baseX, int baseZ, int tx, int groundY, int tz, int species, int treeH) {
        int treeHash = (tx * 374761393) ^ (tz * 668265263);
        Material logMat, leafMat, altLeafMat;

        switch (species) {
            case 2: logMat = Material.BIRCH_LOG; leafMat = Material.BIRCH_LEAVES; altLeafMat = Material.OAK_LEAVES; break;
            case 3: logMat = Material.SPRUCE_LOG; leafMat = Material.SPRUCE_LEAVES; altLeafMat = Material.SPRUCE_LEAVES; break;
            case 4:
                logMat = Material.OAK_LOG;
                leafMat = ((treeHash & 3) == 0) ? Material.FLOWERING_AZALEA_LEAVES : Material.AZALEA_LEAVES;
                altLeafMat = Material.OAK_LEAVES;
                break;
            default:
                logMat = ((treeHash & 1) == 0) ? Material.OAK_LOG : Material.DARK_OAK_LOG;
                leafMat = ((treeHash & 3) == 0) ? Material.DARK_OAK_LEAVES : Material.OAK_LEAVES;
                altLeafMat = Material.AZALEA_LEAVES;
                break;
        }

        int lxTrunk = tx - baseX;
        int lzTrunk = tz - baseZ;
        int trunkTop = Math.max(2, treeH - 2);
        if (lxTrunk >= 0 && lxTrunk < 16 && lzTrunk >= 0 && lzTrunk < 16) {
            chunkData.setRegion(lxTrunk, groundY + 1, lzTrunk, lxTrunk + 1, groundY + trunkTop + 1, lzTrunk + 1, logMat);
        }

        if (species == 3) {
            int crownStart = Math.max(3, treeH / 3);
            int maxR = Math.min(3, Math.max(2, treeH / 5));
            for (int dy = crownStart; dy <= treeH + 1; dy++) {
                float progress = (float) (dy - crownStart) / Math.max(1, (treeH + 1 - crownStart));
                int r = (dy == treeH + 1) ? 0 : Math.max(0, Math.round(maxR * (1.0f - progress * 0.85f)));
                for (int dx = -r; dx <= r; dx++) {
                    int lx = tx + dx - baseX;
                    if (lx < 0 || lx >= 16) continue;
                    for (int dz = -r; dz <= r; dz++) {
                        int lz = tz + dz - baseZ;
                        if (lz < 0 || lz >= 16 || (dx == 0 && dz == 0 && dy <= trunkTop)) continue;
                        if (dx * dx + dz * dz <= r * r + 1 && chunkData.getType(lx, groundY + dy, lz) == Material.AIR) {
                            chunkData.setBlock(lx, groundY + dy, lz, leafMat);
                        }
                    }
                }
            }
        } else {
            int maxR = (species == 4) ? 2 : Math.min(4, Math.max(2, treeH / 4));
            int crownCenterY = (species == 4) ? Math.max(2, treeH - 2) : Math.max(3, treeH - maxR);
            for (int dy = Math.max(2, crownCenterY - maxR + 1); dy <= treeH; dy++) {
                float normY = (float) (dy - crownCenterY) / Math.max(1.0f, (float) maxR);
                float rFloat = maxR * (float) Math.sqrt(Math.max(0.15f, 1.0f - 0.75f * normY * normY));
                int r = Math.min(4, Math.round(rFloat));
                int maxDistSq = Math.round(rFloat * rFloat);

                for (int dx = -r; dx <= r; dx++) {
                    int lx = tx + dx - baseX;
                    if (lx < 0 || lx >= 16) continue;
                    for (int dz = -r; dz <= r; dz++) {
                        int lz = tz + dz - baseZ;
                        if (lz < 0 || lz >= 16 || (dx == 0 && dz == 0 && dy <= trunkTop)) continue;
                        int distSq = dx * dx + dz * dz;
                        if (distSq > maxDistSq + 1) continue;
                        int edgeHash = ((tx + dx) * 198491317) ^ ((tz + dz) * 6542989) ^ (dy * 31337);
                        if (distSq >= maxDistSq && (edgeHash & 3) == 0) continue;
                        if (chunkData.getType(lx, groundY + dy, lz) == Material.AIR) {
                            chunkData.setBlock(lx, groundY + dy, lz, ((edgeHash & 7) == 0) ? altLeafMat : leafMat);
                        }
                    }
                }
            }
        }
    }

    @Override public List<BlockPopulator> getDefaultPopulators(World world) { return Collections.emptyList(); }
    @Override public boolean shouldGenerateNoise() { return false; }
    @Override public boolean shouldGenerateSurface() { return false; }
    @Override public boolean shouldGenerateCaves() { return false; }
    @Override public boolean shouldGenerateDecorations() { return false; }
    @Override public boolean shouldGenerateMobs() { return false; }
    @Override public boolean shouldGenerateStructures() { return false; }
}
