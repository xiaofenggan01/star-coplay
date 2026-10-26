using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.TerrainFeatures;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// Compound handler that plants seeds on all plantable HoeDirt tiles in the current location.
///
/// State machine: Scanning -> NavigatingToTile -> Planting -> (loop)
/// </summary>
public class PlantCropsHandler : ICompoundHandler
{
    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;

    // ── Command context ──
    private int _seq;
    private string _actionId = "";

    // ── Parameters ──
    private string _seed = "";
    private int _maxCount;

    // ── State machine ──
    private enum Phase { Inactive, Scanning, NavigatingToTile, Planting }
    private Phase _phase = Phase.Inactive;

    // ── Work state ──
    private Queue<Point> _targets = new();
    private Point _currentTarget;
    private Point _standTile;           // Adjacent tile to stand on
    private string _startLocation = ""; // Location name at start (warp detection)
    private int _plantedCount;
    private int _skippedCount;
    private string _stoppedReason = "all_done";

    // ── Frame counters for post-action waits ──
    private int _waitFrames;
    private const int PostPlantWaitFrames = 3;

    /// <inheritdoc />
    public bool IsActive => _phase != Phase.Inactive;

    public PlantCropsHandler(ActionExecutor executor, IMonitor monitor)
    {
        _executor = executor;
        _monitor = monitor;
    }

    /// <inheritdoc />
    public V2Result? Start(V2Command cmd)
    {
        _seq = cmd.Seq;
        _actionId = cmd.ActionId;

        // Parse parameters
        if (!CompoundUtils.TryGetString(cmd.Params, "seed", out string seed) || string.IsNullOrWhiteSpace(seed))
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.InvalidParams, "Missing required parameter: seed");
        }
        _seed = seed;

        _maxCount = 50;
        if (CompoundUtils.TryGetInt(cmd.Params, "max_count", out int mc) && mc > 0)
            _maxCount = mc;

        // Reset counters
        _plantedCount = 0;
        _skippedCount = 0;
        _stoppedReason = "all_done";
        _waitFrames = 0;

        _monitor.Log($"[PlantCrops] Start: seed={_seed}, max_count={_maxCount}", LogLevel.Debug);

        // Pre-validation: check active location
        var location = Game1.currentLocation;
        if (location == null)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.ExecutionFailed, "No active location");
        }
        _startLocation = location.NameOrUniqueName;

        // Pre-validation: check seed exists in inventory
        var equipResult = _executor.EquipItemByName(_seed);
        if (!equipResult.Success)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.ItemNotFound, $"Seed '{_seed}' not found in inventory");
        }

        // Scan for plantable tiles
        var rawTiles = CompoundUtils.FindPlantableTiles(location);
        if (rawTiles.Count == 0)
        {
            _monitor.Log("[PlantCrops] No plantable HoeDirt found", LogLevel.Debug);
            return ResultBuilder.Success(_seq, _actionId, new Dictionary<string, object>
            {
                ["planted"] = 0,
                ["skipped"] = 0,
                ["stopped_reason"] = "all_done",
                ["seed"] = _seed,
                ["location"] = location.NameOrUniqueName
            }).WithObservation($"No plantable HoeDirt tiles found (current location: {location.NameOrUniqueName})");
        }

        // Sort by nearest-neighbor greedy order
        var start = Game1.player.TilePoint;
        _targets = CompoundUtils.GreedyNearestSort(start, rawTiles);

        _monitor.Log($"[PlantCrops] Found {_targets.Count} plantable tiles, starting traversal", LogLevel.Debug);

        // Begin navigation to first target
        _phase = Phase.Scanning;
        return null;
    }

    /// <inheritdoc />
    public V2Result? Tick()
    {
        switch (_phase)
        {
            case Phase.Scanning:
                return TickScanning();
            case Phase.NavigatingToTile:
                return TickNavigating();
            case Phase.Planting:
                return TickPlanting();
            default:
                return null;
        }
    }

    /// <inheritdoc />
    public V2Result Cancel()
    {
        _monitor.Log($"[PlantCrops] Cancelled after planting {_plantedCount} crops", LogLevel.Info);
        _executor.CancelCurrent();
        _stoppedReason = "cancelled";
        var result = BuildResult();
        Reset();
        return result;
    }

    // ── Phase: Scanning ──────────────────────────────────────────

    private V2Result? TickScanning()
    {
        // Check max count
        if (_plantedCount >= _maxCount)
            return Complete("max_count");

        // Dequeue next target
        if (_targets.Count == 0)
            return Complete("all_done");

        _currentTarget = _targets.Dequeue();

        // Check location hasn't changed (warp detection)
        if (!string.Equals(Game1.currentLocation?.NameOrUniqueName, _startLocation, StringComparison.OrdinalIgnoreCase))
            return Complete("location_changed");

        // Verify the tile is still plantable
        if (!IsStillPlantable(Game1.currentLocation, _currentTarget))
        {
            _monitor.Log($"[PlantCrops] Tile ({_currentTarget.X},{_currentTarget.Y}) no longer plantable, skipping", LogLevel.Debug);
            _skippedCount++;
            return null; // Stay in Scanning
        }

        // Navigate to an adjacent tile
        var adjacent = CompoundUtils.FindAdjacentWalkable(Game1.currentLocation!, _currentTarget);
        if (adjacent == null)
        {
            _monitor.Log($"[PlantCrops] No walkable tile adjacent to ({_currentTarget.X},{_currentTarget.Y}), skipping", LogLevel.Debug);
            _skippedCount++;
            return null;
        }
        _standTile = adjacent.Value;

        if (!_executor.NavigateTo(_standTile))
        {
            _monitor.Log($"[PlantCrops] Cannot path to ({_standTile.X},{_standTile.Y}), skipping", LogLevel.Debug);
            _skippedCount++;
            return null;
        }

        _phase = Phase.NavigatingToTile;
        _monitor.Log($"[PlantCrops] Navigating to ({_standTile.X},{_standTile.Y}) for tile ({_currentTarget.X},{_currentTarget.Y})", LogLevel.Debug);
        return null;
    }

    // ── Phase: NavigatingToTile ──────────────────────────────────

    private V2Result? TickNavigating()
    {
        if (_executor.IsBusy)
            return null;

        // Verify navigation actually reached near the target
        var playerPt = Game1.player.TilePoint;
        int dist = System.Math.Abs(playerPt.X - _currentTarget.X)
                 + System.Math.Abs(playerPt.Y - _currentTarget.Y);
        if (dist > 3)
        {
            _monitor.Log($"[PlantCrops] Nav ended too far from ({_currentTarget.X},{_currentTarget.Y}), dist={dist}, skipping", LogLevel.Debug);
            _skippedCount++;
            _phase = Phase.Scanning;
            return null;
        }

        // Re-verify the tile is still plantable after navigation
        if (!IsStillPlantable(Game1.currentLocation, _currentTarget))
        {
            _monitor.Log($"[PlantCrops] Tile ({_currentTarget.X},{_currentTarget.Y}) no longer plantable after nav, skipping", LogLevel.Debug);
            _skippedCount++;
            _phase = Phase.Scanning;
            return null;
        }

        // Equip the seed before planting (player may have switched items)
        var equipResult = _executor.EquipItemByName(_seed);
        if (!equipResult.Success)
        {
            _monitor.Log($"[PlantCrops] No '{_seed}' seeds remaining, stopping", LogLevel.Debug);
            return Complete("no_seeds");
        }

        // Face the target tile
        int dir = CompoundUtils.GetFacingDirection(playerPt, _currentTarget);
        Game1.player.faceDirection(dir);

        // Interact to plant the seed
        _executor.InteractWithTile(_currentTarget.X, _currentTarget.Y);
        _plantedCount++;
        _waitFrames = PostPlantWaitFrames;
        _phase = Phase.Planting;

        _monitor.Log($"[PlantCrops] Planting '{_seed}' at ({_currentTarget.X},{_currentTarget.Y}), count={_plantedCount}", LogLevel.Debug);
        return null;
    }

    // ── Phase: Planting (post-action wait) ───────────────────────

    private V2Result? TickPlanting()
    {
        // Wait a few frames for the plant to settle
        if (_waitFrames > 0)
        {
            _waitFrames--;
            return null;
        }

        // Done with this tile, back to Scanning for next
        _phase = Phase.Scanning;
        return null;
    }

    // ── Helpers ──────────────────────────────────────────────────

    private static bool IsStillPlantable(GameLocation? location, Point tile)
    {
        if (location == null) return false;
        if (!location.terrainFeatures.TryGetValue(
                new Vector2(tile.X, tile.Y),
                out var feature))
            return false;

        return feature is HoeDirt dirt && dirt.crop == null;
    }

    // ── Completion helpers ───────────────────────────────────────

    private V2Result Complete(string stoppedReason)
    {
        _monitor.Log($"[PlantCrops] Complete: planted={_plantedCount}, skipped={_skippedCount}, reason={stoppedReason}", LogLevel.Info);
        _stoppedReason = stoppedReason;
        var result = BuildResult();
        Reset();
        return result;
    }

    private V2Result BuildResult()
    {
        var location = Game1.currentLocation;

        var data = new Dictionary<string, object>
        {
            ["planted"] = _plantedCount,
            ["skipped"] = _skippedCount,
            ["stopped_reason"] = _stoppedReason,
            ["seed"] = _seed,
            ["location"] = location?.NameOrUniqueName ?? "Unknown"
        };

        var result = ResultBuilder.Success(_seq, _actionId, data);

        if (_plantedCount > 0)
            result.WithObservation($"Planted {_plantedCount} '{_seed}' in {location?.NameOrUniqueName ?? "Unknown"}.");
        if (_skippedCount > 0)
            result.WithObservation($"Skipped {_skippedCount} tiles (no path or no longer plantable).");
        if (_stoppedReason == "no_seeds")
            result.WithObservation($"Stopped early: no '{_seed}' remaining in inventory.");

        return result;
    }

    private void Reset()
    {
        _phase = Phase.Inactive;
        _targets.Clear();
        _currentTarget = default;
        _standTile = default;
        _startLocation = "";
        _seed = "";
        _plantedCount = 0;
        _skippedCount = 0;
        _stoppedReason = "all_done";
        _waitFrames = 0;
    }
}
