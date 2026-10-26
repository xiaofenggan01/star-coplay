using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.Json;
using Microsoft.Xna.Framework;
using System.Linq;
using StardewValley;
using StardewValley.TerrainFeatures;
using StardewValley.Tools;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// Shared utility methods for compound handlers.
/// </summary>
public static class CompoundUtils
{
    /// <summary>
    /// Greedy nearest-neighbor sort: starting from a point, always pick the closest
    /// unvisited tile next. Produces a near-optimal traversal order for farming tasks.
    /// Uses Manhattan distance (appropriate for tile-based movement).
    /// </summary>
    public static Queue<Point> GreedyNearestSort(Point start, List<Point> targets)
    {
        var result = new Queue<Point>();
        var remaining = new List<Point>(targets);
        var current = start;

        while (remaining.Count > 0)
        {
            int bestIdx = 0;
            int bestDist = int.MaxValue;

            for (int i = 0; i < remaining.Count; i++)
            {
                int d = Math.Abs(remaining[i].X - current.X)
                      + Math.Abs(remaining[i].Y - current.Y);
                if (d < bestDist)
                {
                    bestDist = d;
                    bestIdx = i;
                }
            }

            var chosen = remaining[bestIdx];
            result.Enqueue(chosen);
            current = chosen;
            remaining.RemoveAt(bestIdx);
        }

        return result;
    }

    /// <summary>
    /// Row-first traversal for crop-like grids.
    /// Sorts primarily by Y (row), then by X within the row.
    /// To reduce long cross-map jumps, rows are traversed in a serpentine pattern,
    /// seeded from the player's current row.
    /// </summary>
    public static Queue<Point> RowFirstSort(Point start, List<Point> targets)
    {
        if (targets.Count == 0)
            return new Queue<Point>();

        var rows = targets
            .GroupBy(p => p.Y)
            .OrderBy(g => Math.Abs(g.Key - start.Y))
            .ThenBy(g => g.Key)
            .ToList();

        var ordered = new List<Point>(targets.Count);
        bool leftToRight = true;

        foreach (var row in rows)
        {
            var points = leftToRight
                ? row.OrderBy(p => p.X)
                : row.OrderByDescending(p => p.X);
            ordered.AddRange(points);
            leftToRight = !leftToRight;
        }

        return new Queue<Point>(ordered);
    }

    /// <summary>
    /// Find the nearest water tile in the location (pond, river, well, etc.).
    /// Full map scan — same approach as V1 BatchWateringAction (proven to work).
    /// Ring-based optimization was removed because standard Farm pond can be 60+ tiles
    /// away from crop areas, exceeding the previous maxDistance limit.
    /// </summary>
    public static Point? FindNearestWaterTile(GameLocation location)
    {
        var playerTile = Game1.player.Tile;
        float bestDist = float.MaxValue;
        Point? best = null;

        int mapWidth = location.Map.Layers[0].LayerWidth;
        int mapHeight = location.Map.Layers[0].LayerHeight;
        int waterCount = 0;

        // First pass: isOpenWater (open, fishable water — strict)
        for (int x = 0; x < mapWidth; x++)
        {
            for (int y = 0; y < mapHeight; y++)
            {
                if (!location.isOpenWater(x, y)) continue;

                waterCount++;
                float dist = Math.Abs(x - playerTile.X) + Math.Abs(y - playerTile.Y);
                if (dist < bestDist)
                {
                    bestDist = dist;
                    best = new Point(x, y);
                }
            }
        }

        // Fallback: if no isOpenWater found, try isWaterTile (broader — includes
        // water tiles that may be partially blocked but still usable for refilling)
        if (best == null)
        {
            for (int x = 0; x < mapWidth; x++)
            {
                for (int y = 0; y < mapHeight; y++)
                {
                    if (!location.isWaterTile(x, y)) continue;

                    waterCount++;
                    float dist = Math.Abs(x - playerTile.X) + Math.Abs(y - playerTile.Y);
                    if (dist < bestDist)
                    {
                        bestDist = dist;
                        best = new Point(x, y);
                    }
                }
            }
        }

        return best;
    }

    /// <summary>
    /// Find a reachable water source: iterates water tiles by distance, for each tries
    /// FindAdjacentWalkable, then NavigateTo. Returns the first (waterTile, standTile) pair
    /// where standTile is walkable AND pathable from the player's current position.
    /// Returns null if no reachable water source found.
    /// maxCandidates limits how many water tiles to try before giving up.
    /// </summary>
    public static (Point waterTile, Point standTile)? FindReachableWaterSource(
        GameLocation location, ActionExecutor executor, int maxCandidates = 10)
    {
        var playerTile = Game1.player.Tile;
        int mapWidth = location.Map.Layers[0].LayerWidth;
        int mapHeight = location.Map.Layers[0].LayerHeight;

        // Collect all water tiles with distance
        var candidates = new List<(Point tile, float dist)>();

        for (int x = 0; x < mapWidth; x++)
        {
            for (int y = 0; y < mapHeight; y++)
            {
                if (location.isOpenWater(x, y) || location.isWaterTile(x, y))
                {
                    float dist = Math.Abs(x - playerTile.X) + Math.Abs(y - playerTile.Y);
                    candidates.Add((new Point(x, y), dist));
                }
            }
        }

        if (candidates.Count == 0)
            return null;

        // Sort by distance, try closest first
        candidates.Sort((a, b) => a.dist.CompareTo(b.dist));

        // Deduplicate by cluster: skip water tiles within 2 tiles of an already-tried tile
        // This avoids trying 50 tiles in the same pond
        var tried = new List<Point>();
        int attempts = 0;

        foreach (var (waterTile, dist) in candidates)
        {
            if (attempts >= maxCandidates)
                break;

            // Skip if too close to an already-tried tile (same pond cluster)
            bool tooClose = false;
            foreach (var t in tried)
            {
                if (Math.Abs(t.X - waterTile.X) + Math.Abs(t.Y - waterTile.Y) <= 2)
                {
                    tooClose = true;
                    break;
                }
            }
            if (tooClose) continue;

            tried.Add(waterTile);
            attempts++;

            // Try to find a walkable adjacent tile
            var stand = FindAdjacentWalkable(location, waterTile);
            if (stand == null)
                continue;

            // Try navigation — this does a PathFindController test
            if (executor.NavigateTo(stand.Value))
            {
                // Navigation started! Cancel it — we just wanted to test reachability
                // Actually, don't cancel — let the handler use this navigation
                return (waterTile, stand.Value);
            }
        }

        return null;
    }

    /// <summary>
    /// Find a walkable tile adjacent to the target (4 cardinal directions first, then diagonals).
    /// </summary>
    public static Point? FindAdjacentWalkable(GameLocation location, Point target)
    {
        // Cardinal first (more natural)
        int[][] offsets = new[]
        {
            new[] { 0, -1 }, new[] { 0, 1 }, new[] { -1, 0 }, new[] { 1, 0 },
            new[] { -1, -1 }, new[] { 1, -1 }, new[] { -1, 1 }, new[] { 1, 1 }
        };

        foreach (var off in offsets)
        {
            int x = target.X + off[0];
            int y = target.Y + off[1];
            var v = new Vector2(x, y);

            if (location.isTilePassable(new xTile.Dimensions.Location(x, y), Game1.viewport)
                && !location.isWaterTile(x, y))
            {
                return new Point(x, y);
            }
        }

        return null;
    }

    /// <summary>
    /// Find the nearest uncollected debris item within range of a tile.
    /// After chopping a tree or breaking a rock, drops fly to nearby tiles (1-5 range).
    /// The player must walk within ~2 tiles to auto-pickup. This method scans
    /// location.debris for Chunks and returns the tile position of the nearest one
    /// that is close to the action tile but not yet in player pickup range.
    /// </summary>
    /// <param name="location">The game location to scan</param>
    /// <param name="nearTile">The tile where the action happened (tree/rock position)</param>
    /// <param name="maxRange">Max Manhattan distance from action tile to consider debris (default 6)</param>
    /// <returns>Tile position of nearest debris, or null if none found</returns>
    public static Point? FindNearestDebris(GameLocation location, Point nearTile, int maxRange = 12)
    {
        float bestDist = float.MaxValue;
        Point? best = null;
        var playerTile = Game1.player.Tile;

        foreach (var debris in location.debris)
        {
            if (debris.Chunks == null || debris.Chunks.Count == 0) continue;

            // Use the first chunk's position as representative
            var chunk = debris.Chunks[0];
            int cx = (int)(chunk.position.Value.X / 64f);
            int cy = (int)(chunk.position.Value.Y / 64f);

            // Must be near the action tile (not random debris elsewhere)
            int distFromAction = Math.Abs(cx - nearTile.X) + Math.Abs(cy - nearTile.Y);
            if (distFromAction > maxRange) continue;

            // Already in pickup range? Skip
            float distFromPlayer = Math.Abs(cx - playerTile.X) + Math.Abs(cy - playerTile.Y);
            if (distFromPlayer <= 1.5f) continue;

            if (distFromPlayer < bestDist)
            {
                bestDist = distFromPlayer;
                best = new Point(cx, cy);
            }
        }

        return best;
    }

    /// <summary>
    /// Force-reset player tool/animation state. Use when animation gets stuck.
    /// </summary>
    public static void ForceResetToolState(Farmer player)
    {
        player.UsingTool = false;
        player.canMove = true;
        player.FarmerSprite.StopAnimation();
        player.completelyStopAnimatingOrDoingAction();
    }

    /// <summary>
    /// Calculate facing direction from player to target tile.
    /// 0=up, 1=right, 2=down, 3=left
    /// </summary>
    public static int GetFacingDirection(Point from, Point to)
    {
        int dx = to.X - from.X;
        int dy = to.Y - from.Y;
        if (Math.Abs(dx) > Math.Abs(dy))
            return dx > 0 ? 1 : 3;
        return dy > 0 ? 2 : 0;
    }

    /// <summary>
    /// Get the WateringCan from the player's inventory.
    /// Returns null if not found.
    /// </summary>
    public static WateringCan? FindWateringCan(Farmer player)
    {
        for (int i = 0; i < player.Items.Count; i++)
        {
            if (player.Items[i] is WateringCan can)
                return can;
        }
        return null;
    }

    /// <summary>
    /// Collect all unwatered crop tiles on a location.
    /// Returns deduplicated list of tile positions.
    /// </summary>
    public static List<Point> FindUnwateredCrops(GameLocation location)
    {
        var tiles = new List<Point>();
        var seen = new HashSet<(int, int)>();

        foreach (var pair in location.terrainFeatures.Pairs)
        {
            if (pair.Value is not HoeDirt dirt) continue;
            if (dirt.crop == null) continue;
            if (dirt.state.Value == 1) continue; // Already watered

            int x = (int)pair.Key.X;
            int y = (int)pair.Key.Y;
            if (seen.Add((x, y)))
                tiles.Add(new Point(x, y));
        }

        return tiles;
    }

    /// <summary>
    /// Collect all harvestable crop tiles on a location.
    /// </summary>
    public static List<Point> FindHarvestableCrops(GameLocation location)
    {
        var tiles = new List<Point>();
        var seen = new HashSet<(int, int)>();

        foreach (var pair in location.terrainFeatures.Pairs)
        {
            if (pair.Value is not HoeDirt dirt) continue;
            if (dirt.crop == null || dirt.crop.dead.Value) continue;

            // Use the game engine's own readyForHarvest() method — covers all edge cases
            // (regrowable crops, dayOfCurrentPhase edge values, etc.)
            if (dirt.readyForHarvest())
            {
                int x = (int)pair.Key.X;
                int y = (int)pair.Key.Y;
                if (seen.Add((x, y)))
                    tiles.Add(new Point(x, y));
            }
        }

        return tiles;
    }

    /// <summary>
    /// Collect all plantable HoeDirt tiles on a location.
    /// A tile is plantable if it has HoeDirt with no existing crop.
    /// </summary>
    public static List<Point> FindPlantableTiles(GameLocation location)
    {
        var tiles = new List<Point>();
        var seen = new HashSet<(int, int)>();

        foreach (var pair in location.terrainFeatures.Pairs)
        {
            if (pair.Value is not HoeDirt dirt) continue;
            if (dirt.crop != null) continue; // Already has a crop

            int x = (int)pair.Key.X;
            int y = (int)pair.Key.Y;
            if (seen.Add((x, y)))
                tiles.Add(new Point(x, y));
        }

        return tiles;
    }

    /// <summary>
    /// Collect all choppable tree tiles on a location.
    /// By default only includes mature trees (growthStage >= 5).
    /// When includeStumps is true, also includes stumps (stump.Value == true).
    /// </summary>
    public static List<Point> FindChoppableTrees(GameLocation location, bool includeStumps = false)
    {
        var tiles = new List<Point>();
        var seen = new HashSet<(int, int)>();

        foreach (var pair in location.terrainFeatures.Pairs)
        {
            if (pair.Value is not Tree tree) continue;

            bool isMature = tree.growthStage.Value >= 5;
            bool isStump = tree.stump.Value;

            // Include mature trees always; include stumps only when requested
            if (!isMature && !(includeStumps && isStump)) continue;

            int x = (int)pair.Key.X;
            int y = (int)pair.Key.Y;
            if (seen.Add((x, y)))
                tiles.Add(new Point(x, y));
        }

        return tiles;
    }

    /// <summary>
    /// Get the Axe from the player's inventory.
    /// Returns null if not found.
    /// </summary>
    public static Axe? FindAxe(Farmer player)
    {
        for (int i = 0; i < player.Items.Count; i++)
        {
            if (player.Items[i] is Axe axe)
                return axe;
        }
        return null;
    }

    /// <summary>
    /// Find a tool by category name (e.g. "pickaxe", "axe", "hoe", "watering_can", "scythe").
    /// This allows L3 Skills to specify tool_category instead of hardcoding locale-dependent
    /// display names like "镐" or "铜十字镐" which change after tool upgrades.
    /// Returns null if the category is unknown or the tool is not in inventory.
    /// </summary>
    public static Tool? FindToolByCategory(Farmer player, string category)
    {
        return category?.ToLower() switch
        {
            "pickaxe" => FindPickaxe(player),
            "axe" => FindAxe(player),
            "hoe" => player.Items.FirstOrDefault(i => i is Hoe) as Tool,
            "watering_can" or "wateringcan" => FindWateringCan(player),
            "scythe" => player.Items.FirstOrDefault(i => i is MeleeWeapon w && w.isScythe()) as Tool,
            _ => null
        };
    }

    /// <summary>
    /// Get the Pickaxe from the player's inventory.
    /// Returns null if not found.
    /// </summary>
    public static StardewValley.Tools.Pickaxe? FindPickaxe(Farmer player)
    {
        for (int i = 0; i < player.Items.Count; i++)
        {
            if (player.Items[i] is StardewValley.Tools.Pickaxe pickaxe)
                return pickaxe;
        }
        return null;
    }

    /// <summary>
    /// Collect all mineable rock tiles in a location.
    /// Scans location.objects for entries where Name == "Stone".
    /// Returns a list of tile positions.
    /// </summary>
    public static List<Point> FindMineableRocks(GameLocation location)
    {
        var tiles = new List<Point>();
        var seen = new HashSet<(int, int)>();

        foreach (var pair in location.objects.Pairs)
        {
            var obj = pair.Value;
            if (obj is not StardewValley.Object sObj) continue;
            if (sObj.Name != "Stone") continue;

            int x = (int)pair.Key.X;
            int y = (int)pair.Key.Y;
            if (seen.Add((x, y)))
                tiles.Add(new Point(x, y));
        }

        return tiles;
    }

    /// <summary>
    /// Get the number of axe strikes needed to fell a mature tree, based on axe upgrade level.
    /// Source: Stardew Valley Wiki — Tools page.
    /// </summary>
    /// <param name="axeUpgradeLevel">0=basic, 1=copper, 2=steel, 3=gold, 4=iridium</param>
    /// <param name="isStump">True if targeting a tree stump (half the hits of a full tree)</param>
    /// <returns>Number of strikes needed</returns>
    public static int GetTreeStrikesNeeded(int axeUpgradeLevel, bool isStump = false)
    {
        int strikes = axeUpgradeLevel switch
        {
            0 => 10,  // Basic Axe
            1 => 8,   // Copper Axe
            2 => 6,   // Steel Axe
            3 => 4,   // Gold Axe
            _ => 2,   // Iridium Axe (level 4+)
        };
        return isStump ? System.Math.Max(1, strikes / 2) : strikes;
    }

    /// <summary>
    /// Get the number of pickaxe strikes needed to break a farm stone (location.objects, Name=="Stone").
    /// Farm stones are always 1 hit regardless of pickaxe level.
    /// Mine/quarry stones vary — but this handler only targets farm stones for now.
    /// </summary>
    public static int GetFarmRockStrikesNeeded()
    {
        return 1;
    }

    /// <summary>
    /// Try to parse an integer from V2Command params.
    /// Handles int, long, double, float, string, JsonElement.
    /// </summary>
    public static bool TryGetInt(Dictionary<string, object> args, string key, out int value)
    {
        value = 0;
        if (!args.TryGetValue(key, out var obj) || obj == null)
            return false;

        switch (obj)
        {
            case int i:
                value = i;
                return true;
            case long l when l >= int.MinValue && l <= int.MaxValue:
                value = (int)l;
                return true;
            case double d when d >= int.MinValue && d <= int.MaxValue:
                value = (int)d;
                return true;
            case float f when f >= int.MinValue && f <= int.MaxValue:
                value = (int)f;
                return true;
            case string s when int.TryParse(s, NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed):
                value = parsed;
                return true;
            case JsonElement element when element.ValueKind == JsonValueKind.Number:
                if (element.TryGetInt32(out var parsedInt))
                {
                    value = parsedInt;
                    return true;
                }
                break;
            case JsonElement element when element.ValueKind == JsonValueKind.String:
                if (int.TryParse(element.GetString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsedStr))
                {
                    value = parsedStr;
                    return true;
                }
                break;
        }
        return false;
    }

    /// <summary>
    /// Try to parse a string from V2Command params.
    /// </summary>
    public static bool TryGetString(Dictionary<string, object> args, string key, out string value)
    {
        value = "";
        if (!args.TryGetValue(key, out var obj) || obj == null)
            return false;

        if (obj is string s)
        {
            value = s;
            return true;
        }
        if (obj is JsonElement element && element.ValueKind == JsonValueKind.String)
        {
            value = element.GetString() ?? "";
            return !string.IsNullOrEmpty(value);
        }
        return false;
    }

    /// <summary>
    /// Try to parse a boolean from V2Command params.
    /// Handles bool, string ("true"/"false"), JsonElement.
    /// </summary>
    public static bool TryGetBool(Dictionary<string, object> args, string key, out bool value)
    {
        value = false;
        if (!args.TryGetValue(key, out var obj) || obj == null)
            return false;

        switch (obj)
        {
            case bool b:
                value = b;
                return true;
            case string s when bool.TryParse(s, out var parsed):
                value = parsed;
                return true;
            case JsonElement element when element.ValueKind == JsonValueKind.True:
                value = true;
                return true;
            case JsonElement element when element.ValueKind == JsonValueKind.False:
                value = false;
                return true;
            case JsonElement element when element.ValueKind == JsonValueKind.String:
                if (bool.TryParse(element.GetString(), out var parsedStr))
                {
                    value = parsedStr;
                    return true;
                }
                break;
        }
        return false;
    }

}
