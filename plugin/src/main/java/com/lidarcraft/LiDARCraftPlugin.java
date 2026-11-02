package com.lidarcraft;

import net.md_5.bungee.api.ChatMessageType;
import net.md_5.bungee.api.chat.ClickEvent;
import net.md_5.bungee.api.chat.ComponentBuilder;
import net.md_5.bungee.api.chat.HoverEvent;
import net.md_5.bungee.api.chat.TextComponent;
import org.bukkit.Bukkit;
import org.bukkit.ChatColor;
import org.bukkit.GameRule;
import org.bukkit.Location;
import org.bukkit.World;
import org.bukkit.WorldCreator;
import org.bukkit.command.Command;
import org.bukkit.command.CommandExecutor;
import org.bukkit.command.CommandSender;
import org.bukkit.command.PluginCommand;
import org.bukkit.command.TabCompleter;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.generator.ChunkGenerator;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.scheduler.BukkitRunnable;

import java.io.BufferedReader;
import java.io.File;
import java.io.InputStreamReader;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

public class LiDARCraftPlugin extends JavaPlugin implements CommandExecutor, TabCompleter, Listener {

    public static final String WORLD_NAME = "malvern";
    private static final List<String> FEATURES = Arrays.asList("roads", "cliffs", "trees", "buildings", "hedges", "flora", "slabs");
    private static final String[] COMPASS_DIRS = {"S", "SW", "W", "NW", "N", "NE", "E", "SE"};

    private int originEasting = 366000;
    private int originNorthing = 252384;
    private int datumShift = 104;

    private final class Landmark {
        final String key, label;
        final int x, z;
        final float yaw;

        Landmark(String key, String label, int easting, int northing, float yaw) {
            this.key = key;
            this.label = label;
            this.x = easting - originEasting;
            this.z = originNorthing - northing;
            this.yaw = yaw;
        }
    }

    private final Map<String, Landmark> landmarks = new LinkedHashMap<>();
    private LiDARCraftChunkGenerator generator;
    private World lidarWorld;

    @Override
    public void onEnable() {
        saveDefaultConfig();
        loadGridConfig();

        File tilesDir = new File(getDataFolder(), "tiles");
        tilesDir.mkdirs();

        this.generator = new LiDARCraftChunkGenerator(tilesDir, datumShift);
        applyConfigFlags();

        PluginCommand lidarCmd = getCommand("lidar");
        if (lidarCmd != null) {
            lidarCmd.setExecutor(this);
            lidarCmd.setTabCompleter(this);
        }
        PluginCommand speedCmd = getCommand("speed");
        if (speedCmd != null) {
            speedCmd.setExecutor(this);
        }
        Bukkit.getPluginManager().registerEvents(this, this);

        Bukkit.getScheduler().runTask(this, () -> {
            initWorld();
            startHudTask();
            for (Player p : Bukkit.getOnlinePlayers()) {
                p.setFlySpeed(0.45f);
            }
        });
    }

    private void loadGridConfig() {
        reloadConfig();
        this.originEasting = getConfig().getInt("grid.origin_easting", 366000);
        this.originNorthing = getConfig().getInt("grid.origin_northing", 252384);
        this.datumShift = getConfig().getInt("grid.datum_shift", 104);
        registerLandmarks();
    }

    private void registerLandmarks() {
        landmarks.clear();
        File lmFile = new File(getDataFolder(), "landmarks.json");
        if (!lmFile.exists()) return;
        try {
            String json = Files.readString(lmFile.toPath());
            Matcher m = Pattern.compile("\\{\\s*\"key\"\\s*:\\s*\"([^\"]+)\"\\s*,\\s*\"label\"\\s*:\\s*\"([^\"]+)\"\\s*,\\s*\"easting\"\\s*:\\s*(-?\\d+)\\s*,\\s*\"northing\"\\s*:\\s*(-?\\d+)\\s*\\}").matcher(json);
            while (m.find()) {
                String k = m.group(1);
                landmarks.putIfAbsent(k, new Landmark(k, m.group(2), Integer.parseInt(m.group(3)), Integer.parseInt(m.group(4)), 180f));
            }
        } catch (Exception ignored) {
        }
    }

    private void applyConfigFlags() {
        if (generator == null) return;
        generator.setRoadsEnabled(getConfig().getBoolean("features.roads", true));
        generator.setCliffsEnabled(getConfig().getBoolean("features.cliffs", true));
        generator.setTreesEnabled(getConfig().getBoolean("features.trees", true));
        generator.setBuildingsEnabled(getConfig().getBoolean("features.buildings", true));
        generator.setHedgesEnabled(getConfig().getBoolean("features.hedges", true));
        generator.setFloraEnabled(getConfig().getBoolean("features.flora", true));
        generator.setSlabsEnabled(getConfig().getBoolean("features.slabs", true));
    }

    private void initWorld() {
        this.lidarWorld = Bukkit.createWorld(new WorldCreator(WORLD_NAME).generator(this.generator));
        if (this.lidarWorld != null) {
            this.lidarWorld.setGameRule(GameRule.DO_DAYLIGHT_CYCLE, false);
            this.lidarWorld.setGameRule(GameRule.DO_WEATHER_CYCLE, false);
            this.lidarWorld.setGameRule(GameRule.DO_MOB_SPAWNING, false);
            this.lidarWorld.setTime(6000L);

            Landmark spawn = getDefaultLandmark();
            if (spawn != null) {
                this.lidarWorld.setSpawnLocation(spawn.x, generator.getSurfaceY(spawn.x, spawn.z) + 1, spawn.z);
            }
        }
    }

    private Landmark getDefaultLandmark() {
        Landmark lm = landmarks.get(getConfig().getString("grid.default_spawn", "wyche"));
        return (lm != null) ? lm : landmarks.values().stream().findFirst().orElse(null);
    }

    private static String yawToCompass(float yaw) {
        return COMPASS_DIRS[Math.floorMod(Math.round(yaw / 45f), 8)];
    }

    private static String bearingToCompass(int dx, int dz) {
        String[] dirs = {"N", "NE", "E", "SE", "S", "SW", "W", "NW"};
        return dirs[Math.floorMod((int) Math.round(Math.toDegrees(Math.atan2(dx, -dz)) / 45.0), 8)];
    }

    private static String formatDistance(double m) {
        return (m < 1000.0) ? ((int) Math.round(m)) + "m" : String.format("%.1fkm", m / 1000.0);
    }

    private void startHudTask() {
        new BukkitRunnable() {
            @Override
            public void run() {
                if (lidarWorld == null || generator == null) return;
                for (Player p : lidarWorld.getPlayers()) {
                    Location loc = p.getLocation();
                    int x = loc.getBlockX(), z = loc.getBlockZ(), y = loc.getBlockY();
                    int elevAOD = y + generator.getDatumShiftAt(x, z);
                    int elevFeet = (int) Math.round(elevAOD * 3.28084);

                    String msg = ChatColor.GOLD + yawToCompass(loc.getYaw()) +
                            ChatColor.GRAY + " | " +
                            ChatColor.AQUA + elevAOD + "m AOD " + ChatColor.GRAY + "(" + elevFeet + " ft) | " +
                            ChatColor.GREEN + getNearestLandmarkHud(x, z);
                    p.spigot().sendMessage(ChatMessageType.ACTION_BAR, new TextComponent(msg));
                }
            }
        }.runTaskTimer(this, 10L, 10L);
    }

    private String getNearestLandmarkHud(int x, int z) {
        Landmark best = null;
        double bestDist = Double.MAX_VALUE;
        for (Landmark lm : landmarks.values()) {
            double d = Math.hypot(x - lm.x, z - lm.z);
            if (d < bestDist) {
                bestDist = d;
                best = lm;
            }
        }
        if (best == null) return "LiDARCraft";
        if (bestDist < 40.0) return best.label;
        return best.label + ChatColor.GRAY + " (" + formatDistance(bestDist) + " " + bearingToCompass(best.x - x, best.z - z) + ")";
    }

    @EventHandler
    public void onPlayerJoin(PlayerJoinEvent event) {
        Player p = event.getPlayer();
        Bukkit.getScheduler().runTaskLater(this, () -> {
            if (!p.isOnline() || lidarWorld == null) return;
            p.setFlySpeed(0.45f);
            Landmark def = getDefaultLandmark();
            if (def != null) teleportToCoords(p, def.label, def.x, def.z, def.yaw);
            sendMenu(p);
        }, 15L);
    }

    @Override
    public ChunkGenerator getDefaultWorldGenerator(String worldName, String id) {
        return this.generator;
    }

    private void sendMenu(CommandSender sender) {
        sender.sendMessage(ChatColor.GOLD + "=== LiDARCraft 1:1 World ===");
        if (sender instanceof Player) {
            Player p = (Player) sender;
            TextComponent row = new TextComponent(ChatColor.GRAY + "Layers: ");
            row.addExtra(makeTogglePill("roads", generator.isRoadsEnabled()));
            row.addExtra(makeTogglePill("cliffs", generator.isCliffsEnabled()));
            row.addExtra(makeTogglePill("trees", generator.isTreesEnabled()));
            row.addExtra(makeTogglePill("buildings", generator.isBuildingsEnabled()));
            row.addExtra(makeTogglePill("hedges", generator.isHedgesEnabled()));
            row.addExtra(makeTogglePill("flora", generator.isFloraEnabled()));
            row.addExtra(makeTogglePill("slabs", generator.isSlabsEnabled()));
            p.spigot().sendMessage(row);

            sendPlaceList(p, "", 8);
            p.sendMessage(ChatColor.GRAY + "Go anywhere: " + ChatColor.YELLOW + "/lc <place | postcode | address | lat,lon>" +
                    ChatColor.GRAY + "  |  Speed: " + ChatColor.YELLOW + "/speed <1-10>");
        } else {
            sender.sendMessage("Places (" + landmarks.size() + "): " + String.join(", ", landmarks.keySet()));
        }
    }

    private TextComponent makeTogglePill(String feat, boolean enabled) {
        TextComponent pill = new TextComponent((enabled ? ChatColor.GREEN : ChatColor.RED) + "[" + feat + "] ");
        pill.setClickEvent(new ClickEvent(ClickEvent.Action.RUN_COMMAND, "/lc toggle " + feat));
        pill.setHoverEvent(new HoverEvent(HoverEvent.Action.SHOW_TEXT,
                new ComponentBuilder(ChatColor.YELLOW + "Click to toggle " + feat + " (" + (enabled ? "ON" : "OFF") + ")").create()));
        return pill;
    }

    private void sendPlaceList(Player p, String filter, int maxCount) {
        int px = p.getLocation().getBlockX(), pz = p.getLocation().getBlockZ();
        String q = filter.toLowerCase().trim();

        List<Landmark> sorted = landmarks.values().stream()
                .filter(lm -> q.isEmpty() || lm.key.contains(q) || lm.label.toLowerCase().contains(q))
                .sorted(Comparator.comparingDouble(lm -> Math.hypot(px - lm.x, pz - lm.z)))
                .collect(Collectors.toList());

        if (sorted.isEmpty()) {
            p.sendMessage(ChatColor.YELLOW + "No saved place '" + filter + "' yet — type " +
                    ChatColor.AQUA + "/lc " + filter + ChatColor.YELLOW + " to build & teleport there!");
            return;
        }

        int shown = Math.min(maxCount, sorted.size());
        TextComponent row = new TextComponent(ChatColor.GRAY + "Places: ");
        for (int i = 0; i < shown; i++) {
            Landmark lm = sorted.get(i);
            double dist = Math.hypot(px - lm.x, pz - lm.z);
            TextComponent pill = new TextComponent(ChatColor.AQUA + "[" + lm.label + " " + ChatColor.GRAY + formatDistance(dist) + "] ");
            pill.setClickEvent(new ClickEvent(ClickEvent.Action.RUN_COMMAND, "/lc " + lm.key));
            pill.setHoverEvent(new HoverEvent(HoverEvent.Action.SHOW_TEXT,
                    new ComponentBuilder(ChatColor.GREEN + "Teleport to " + lm.label).create()));
            row.addExtra(pill);
        }
        if (sorted.size() > shown) {
            TextComponent more = new TextComponent(ChatColor.YELLOW + "[+" + (sorted.size() - shown) + " more]");
            more.setClickEvent(new ClickEvent(ClickEvent.Action.RUN_COMMAND, "/lc places"));
            more.setHoverEvent(new HoverEvent(HoverEvent.Action.SHOW_TEXT,
                    new ComponentBuilder(ChatColor.YELLOW + "Show all " + sorted.size() + " saved places").create()));
            row.addExtra(more);
        }
        p.spigot().sendMessage(row);
    }

    @Override
    public List<String> onTabComplete(CommandSender sender, Command command, String alias, String[] args) {
        if (!"lidar".equalsIgnoreCase(command.getName())) return Collections.emptyList();
        if (args.length == 1) {
            String p = args[0].toLowerCase();
            List<String> suggestions = new ArrayList<>();
            for (String s : Arrays.asList("places", "toggle", "speed")) {
                if (s.startsWith(p)) suggestions.add(s);
            }
            landmarks.keySet().stream().filter(k -> k.startsWith(p)).limit(25).forEach(suggestions::add);
            return suggestions;
        }
        if (args.length == 2) {
            String sub = args[0].toLowerCase();
            String p = args[1].toLowerCase();
            if ("toggle".equals(sub)) {
                return FEATURES.stream().filter(f -> f.startsWith(p)).collect(Collectors.toList());
            }
            if ("tp".equals(sub) || "places".equals(sub)) {
                return landmarks.keySet().stream().filter(k -> k.startsWith(p)).limit(25).collect(Collectors.toList());
            }
            if ("speed".equals(sub)) {
                return Arrays.asList("1", "2", "3", "5", "8", "10");
            }
        }
        return Collections.emptyList();
    }

    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if ("speed".equalsIgnoreCase(command.getName())) {
            return handleSpeed(sender, (args.length > 0) ? args[0] : "5");
        }
        if (lidarWorld == null) return true;

        if (args.length == 0 || "info".equalsIgnoreCase(args[0])) {
            sendMenu(sender);
            return true;
        }

        String sub = args[0].toLowerCase();
        if ("places".equals(sub) || "list".equals(sub)) {
            String filter = (args.length > 1) ? String.join(" ", Arrays.copyOfRange(args, 1, args.length)) : "";
            if (sender instanceof Player) sendPlaceList((Player) sender, filter, 35);
            return true;
        }

        if ("speed".equals(sub)) {
            return handleSpeed(sender, (args.length > 1) ? args[1] : "5");
        }

        if ("reload".equals(sub)) {
            loadGridConfig();
            if (generator != null) generator.clearTileCache();
            sender.sendMessage(ChatColor.GREEN + "Reloaded " + landmarks.size() + " places and tile cache.");
            return true;
        }

        if ("toggle".equals(sub) && args.length >= 2) {
            String feat = args[1].toLowerCase();
            boolean newState;
            switch (feat) {
                case "roads": newState = !generator.isRoadsEnabled(); generator.setRoadsEnabled(newState); break;
                case "cliffs": newState = !generator.isCliffsEnabled(); generator.setCliffsEnabled(newState); break;
                case "trees": newState = !generator.isTreesEnabled(); generator.setTreesEnabled(newState); break;
                case "buildings": newState = !generator.isBuildingsEnabled(); generator.setBuildingsEnabled(newState); break;
                case "hedges": newState = !generator.isHedgesEnabled(); generator.setHedgesEnabled(newState); break;
                case "flora": newState = !generator.isFloraEnabled(); generator.setFloraEnabled(newState); break;
                case "slabs": newState = !generator.isSlabsEnabled(); generator.setSlabsEnabled(newState); break;
                default:
                    sender.sendMessage(ChatColor.RED + "Layers: " + String.join(", ", FEATURES));
                    return true;
            }
            getConfig().set("features." + feat, newState);
            saveConfig();
            if (sender instanceof Player) sendMenu(sender);
            return true;
        }

        // Any other argument (or "/lc tp <target>") teleports directly or builds on demand!
        if (sender instanceof Player) {
            String query = "tp".equals(sub) && args.length > 1
                    ? String.join(" ", Arrays.copyOfRange(args, 1, args.length))
                    : String.join(" ", args);
            teleportOrBuild((Player) sender, query);
        }
        return true;
    }

    private boolean handleSpeed(CommandSender sender, String valStr) {
        if (!(sender instanceof Player)) return true;
        Player p = (Player) sender;
        try {
            float mult = Float.parseFloat(valStr);
            float flySpeed = Math.max(0.05f, Math.min(1.0f, mult * 0.1f));
            p.setFlySpeed(flySpeed);
            p.setWalkSpeed(Math.max(0.1f, Math.min(1.0f, Math.min(5.0f, mult) * 0.2f)));
            p.sendMessage(ChatColor.GREEN + "Fly speed set to " + ChatColor.BOLD + String.format("%.1fx", flySpeed * 10f));
        } catch (NumberFormatException e) {
            p.sendMessage(ChatColor.RED + "Usage: /speed <1-10>");
        }
        return true;
    }

    private File locateBuilderScript() {
        File serverRoot = getDataFolder().getParentFile().getParentFile();
        File parentRoot = serverRoot.getParentFile();
        for (File c : Arrays.asList(
                new File(serverRoot, "build_map.py"),
                new File(parentRoot, "LiDARCraft/build_map.py"),
                new File(parentRoot, "MalvernHills/build_map.py")
        )) {
            if (c.exists()) return c;
        }
        return null;
    }

    private void teleportOrBuild(Player p, String rawTarget) {
        loadGridConfig();
        String keyLookup = rawTarget.replaceAll("[^a-zA-Z0-9]", "").toLowerCase();
        Landmark lm = landmarks.get(keyLookup);
        if (lm != null && generator.hasTileAtBlock(lm.x, lm.z)) {
            teleportToCoords(p, lm.label, lm.x, lm.z, lm.yaw);
            return;
        }

        File scriptFile = locateBuilderScript();
        if (scriptFile == null) {
            p.sendMessage(ChatColor.RED + "Unknown place '" + rawTarget + "'. Try " + ChatColor.YELLOW + "/lc places");
            return;
        }

        p.sendMessage(ChatColor.YELLOW + "Building 1:1 LiDAR + OSM map for '" + rawTarget + "'...");
        Bukkit.getScheduler().runTaskAsynchronously(this, () -> {
            for (String pyBin : Arrays.asList("python", "python3")) {
                try {
                    ProcessBuilder pb = new ProcessBuilder(pyBin, "-u", scriptFile.getAbsolutePath(), rawTarget, "--size", "2");
                    pb.redirectErrorStream(true);
                    Process proc = pb.start();
                    try (BufferedReader reader = new BufferedReader(new InputStreamReader(proc.getInputStream()))) {
                        String line;
                        while ((line = reader.readLine()) != null) {
                            final String logLine = line.trim();
                            if (logLine.startsWith("Location") || logLine.startsWith("[")) {
                                Bukkit.getScheduler().runTask(this, () -> p.sendMessage(ChatColor.GRAY + "  " + logLine));
                            }
                        }
                    }
                    if (proc.waitFor() == 0) {
                        Bukkit.getScheduler().runTask(this, () -> {
                            loadGridConfig();
                            generator.clearTileCache();
                            Landmark built = landmarks.get(keyLookup);
                            if (built != null) {
                                teleportToCoords(p, built.label, built.x, built.z, built.yaw);
                            } else {
                                Landmark last = null;
                                for (Landmark l : landmarks.values()) last = l;
                                if (last != null) teleportToCoords(p, last.label, last.x, last.z, last.yaw);
                            }
                        });
                        return;
                    }
                } catch (Exception ignored) {
                }
            }
            Bukkit.getScheduler().runTask(this, () -> p.sendMessage(ChatColor.RED + "Could not find location '" + rawTarget + "'."));
        });
    }

    private void teleportToCoords(Player p, String label, int x, int z, float yaw) {
        int y = generator.getSurfaceY(x, z) + 12;
        p.teleport(new Location(lidarWorld, x + 0.5, y, z + 0.5, yaw, 5f));
        p.setAllowFlight(true);
        p.setFlying(true);
        p.sendMessage(ChatColor.GREEN + "Teleported to " + ChatColor.BOLD + label);
    }
}
