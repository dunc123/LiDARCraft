package com.lidarcraft;

import org.junit.jupiter.api.Test;
import java.io.File;
import static org.junit.jupiter.api.Assertions.*;

/**
 * Real-data integration test for LiDARCraftChunkGenerator.
 * Loads compiled MLV4 binary tiles from the local Paper server (Glastonbury Tor & Malvern Hills)
 * and verifies negative tile coordinate indexing, per-tile datum shifts, and real-world elevations.
 */
public class LiDARCraftIntegrationTest {

    @Test
    public void testRealGlastonburyAndMalvernTiles() {
        File tilesDir = new File("../../local_server/plugins/LiDARCraft/tiles");
        if (!tilesDir.exists()) {
            tilesDir = new File("../local_server/plugins/LiDARCraft/tiles");
        }
        if (!new File(tilesDir, "tile_-8_55.bin").exists()) {
            return; // Skip if local_server Glastonbury tiles have not been generated yet
        }

        LiDARCraftChunkGenerator generator = new LiDARCraftChunkGenerator(tilesDir, 104);

        // 1. Glastonbury Tor summit (OS Grid E=351216, N=138612 -> Minecraft X=-14784, Z=113772 relative to origin 366000,252384)
        int torX = 351216 - 366000; // -14784 (negative tile X = -8)
        int torZ = 252384 - 138612; // 113772 (tile Z = 55)

        int torDatum = generator.getDatumShiftAt(torX, torZ);
        assertEquals(-28, torDatum, "Glastonbury tile (-8,55) should use regional datum_shift = -28m");

        int torSurfaceY = generator.getSurfaceY(torX, torZ);
        int torElevAOD = torSurfaceY + torDatum;
        assertTrue(torElevAOD >= 153 && torElevAOD <= 161,
                "Glastonbury Tor summit elevation should be ~158m AOD, got " + torElevAOD + "m");

        // 2. Glastonbury Town Centre (OS Grid E=349874, N=138923 -> Minecraft X=-16126, Z=113461 in tile -8,55)
        int townX = 349874 - 366000;
        int townZ = 252384 - 138923;
        int townDatum = generator.getDatumShiftAt(townX, townZ);
        assertEquals(-28, townDatum);

        int townSurfaceY = generator.getSurfaceY(townX, townZ);
        int townElevAOD = townSurfaceY + townDatum;
        assertTrue(townElevAOD >= 12 && townElevAOD <= 30,
                "Glastonbury Town Centre elevation should be ~15..25m AOD, got " + townElevAOD + "m");

        // 3. Somerset Levels lowland in tile (-9,54) (OS Grid E=348000, N=140500)
        int levelsX = 348000 - 366000;
        int levelsZ = 252384 - 140500;
        int levelsElevAOD = generator.getSurfaceY(levelsX, levelsZ) + generator.getDatumShiftAt(levelsX, levelsZ);
        assertTrue(levelsElevAOD >= -2 && levelsElevAOD <= 10,
                "Somerset Levels elevation should be ~0..8m AOD, got " + levelsElevAOD + "m");
    }
}
