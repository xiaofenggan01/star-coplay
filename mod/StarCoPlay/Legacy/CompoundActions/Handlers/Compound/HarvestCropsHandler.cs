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
/// Compound handler that harvests all harvestable crops in the current location.
/// Optionally replants seeds after each harvest.
///
/// State machine: Scanning -> NavigatingToTile -> Harvesting -> [Replanting] -> ... -> Complete
/// </summary>
public class HarvestCropsHandler : ICompoundHandler
{
    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;

    // ── Command context ──
    private int _seq;
    private string _actionId = "";

    // ── Parameters ──
    private int _maxCount;
    private string? _replantSeed;

    // ── State machine ──
    private enum Phase { Inactive, Scanning, NavigatingToTile, Harvesting, Replanting, Complete }
    private Phase _phase = Phase.Inactive;

    // ── Work state ──
    private Queue<Point> _targets = new();
    private Point _currentTarget;
    private Point _standTile;          // Adjacent tile to stand on
    private string _startLocation = ""; // Location name at start (warp detection)
    private int _harvestedCount;
    private int _replantedCount;
    private int _skippedCount;
    private string _stoppedReason = "all_done";
    private bool _noSeedsLeft;

    // ── Frame counters for post-action waits ──
    private int _waitFrames;
    private int _harvestWaitFrames;
    private const int PostReplantWaitFrames = 3;
    private const int MinPostHarvestWaitFrames = 3;
    private const int HarvestAnimationTimeoutFrames = 90;

    /// <inheritdoc />
    public bool IsActive => _phase != Phase.Inactive && _phase != Phase.Complete;

    public HarvestCropsHandler(ActionExecutor executor, IMonitor monitor)
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
        _maxCount = 50;
        if (CompoundUtils.TryGetInt(cmd.Params, "max_count", out int mc) && mc > 0)
            _maxCount = mc;

        _replantSeed = null;
        if (CompoundUtils.TryGetString(cmd.Params, "replant_seed", out string seed))
            _replantSeed = seed;

        // Reset counters
        _harvestedCount = 0;
        _replantedCount = 0;
        _skippedCount = 0;
        _stoppedReason = "all_done";
        _noSeedsLeft = false;
        _waitFrames = 0;
        _harvestWaitFrames = 0;

        _monitor.Log($"[HarvestCrops] Start: max_count={_maxCount}, replant_seed={_replantSeed ?? "(none)"}", LogLevel.Debug);

        // Scan for harvestable crops immediately
        var location = Game1.currentLocation;
        if (location == null)
        {
            _phase = Phase.Inactive;
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.ExecutionFailed, "No active location");
        }
        _startLocation = location.NameOrUniqueName;
        var rawTiles = CompoundUtils.FindHarvestableCrops(location);

        if (rawTiles.Count == 0)
        {
            _monitor.Log("[HarvestCrops] No harvestable crops found", LogLevel.Debug);
            _phase = Phase.Inactive;
            return ResultBuilder.Success(_seq, _actionId, new Dictionary<string, object>
            {
                ["harvested"] = 0,
                ["replanted"] = 0,
                ["skipped"] = 0,
                ["stopped_reason"] = "all_done",
                ["location"] = location.NameOrUniqueName
            }).WithObservation($"No harvestable crops found (current location: {location.NameOrUniqueName})");
        }

        // Sort by row-first order to keep harvest progression predictable for crop grids.
        var start = Game1.player.TilePoint;
        _targets = CompoundUtils.RowFirstSort(start, rawTiles);

        _monitor.Log($"[HarvestCrops] Found {_targets.Count} harvestable crops, starting traversal", LogLevel.Debug);

        // Begin navigation to first target
        _phase = Phase.Scanning;
        AdvanceToNextTarget();

        return IsActive ? null : BuildResult();
    }

    /// <inheritdoc />
    public V2Result? Tick()
    {
        if (!IsActive)
            return null;

        switch (_phase)
        {
            case Phase.NavigatingToTile:
                return TickNavigating();

            case Phase.Harvesting:
                return TickHarvesting();

            case Phase.Replanting:
                return TickReplanting();

            default:
                return null;
        }
    }

    /// <inheritdoc />
    public V2Result Cancel()
    {
        _monitor.Log($"[HarvestCrops] Cancelled after harvesting {_harvestedCount} crops", LogLevel.Info);
        _executor.CancelCurrent();
        _stoppedReason = "cancelled";
        _phase = Phase.Inactive;
        return BuildResult();
    }

    // ── Phase handlers ──────────────────────────────────────────────

    private V2Result? TickNavigating()
    {
        if (_executor.IsBusy)
            return null; // Still moving

        // Verify navigation actually reached near the target
        var playerPt = Game1.player.TilePoint;
        int dist = System.Math.Abs(playerPt.X - _currentTarget.X)
                 + System.Math.Abs(playerPt.Y - _currentTarget.Y);
        if (dist > 3)
        {
            _monitor.Log($"[HarvestCrops] Nav ended too far from ({_currentTarget.X},{_currentTarget.Y}), dist={dist}, skipping", LogLevel.Debug);
            _skippedCount++;
            AdvanceToNextTarget();
            return IsActive ? null : BuildResult();
        }

        _monitor.Log($"[HarvestCrops] Arrived near ({_currentTarget.X},{_currentTarget.Y})", LogLevel.Debug);

        // Face the target tile
        int dir = CompoundUtils.GetFacingDirection(playerPt, _currentTarget);
        Game1.player.faceDirection(dir);

        // Verify the crop is still harvestable (another player might have harvested it)
        var location = Game1.currentLocation;
        if (!IsStillHarvestable(location, _currentTarget))
        {
            _monitor.Log($"[HarvestCrops] Tile ({_currentTarget.X},{_currentTarget.Y}) no longer harvestable, skipping", LogLevel.Debug);
            _skippedCount++;
            AdvanceToNextTarget();
            return IsActive ? null : BuildResult();
        }

        // Interact to harvest
        _executor.InteractWithTile(_currentTarget.X, _currentTarget.Y);
        _harvestWaitFrames = 0;
        _phase = Phase.Harvesting;

        _monitor.Log($"[HarvestCrops] Harvesting tile ({_currentTarget.X},{_currentTarget.Y})", LogLevel.Debug);
        return null;
    }

    private V2Result? TickHarvesting()
    {
        _harvestWaitFrames++;

        if (_harvestWaitFrames < MinPostHarvestWaitFrames)
            return null;

        bool readyToContinue = Game1.player.canMove && Game1.activeClickableMenu == null;
        if (!readyToContinue && _harvestWaitFrames < HarvestAnimationTimeoutFrames)
            return null;

        if (!readyToContinue)
        {
            _monitor.Log(
                $"[HarvestCrops] Harvest wait timeout at ({_currentTarget.X},{_currentTarget.Y}); forcing continuation. canMove={Game1.player.canMove}, menu={(Game1.activeClickableMenu?.GetType().Name ?? "null")}",
                LogLevel.Warn);
            if (Game1.activeClickableMenu != null)
                Game1.activeClickableMenu = null;
        }

        if (!IsStillHarvestable(Game1.currentLocation, _currentTarget))
        {
            _harvestedCount++;
        }
        else
        {
            _monitor.Log(
                $"[HarvestCrops] Tile ({_currentTarget.X},{_currentTarget.Y}) still harvestable after interact, counting as skipped",
                LogLevel.Warn);
            _skippedCount++;
        }

        // Check stop conditions after harvest
        int usedSlots = Game1.player.Items.Count(i => i != null);
        if (usedSlots >= Game1.player.Items.Count)
        {
            _stoppedReason = "inventory_full";
            _monitor.Log("[HarvestCrops] Inventory full, stopping", LogLevel.Debug);
            _phase = Phase.Complete;
            return BuildResult();
        }

        if (_harvestedCount >= _maxCount)
        {
            _stoppedReason = "max_count";
            _monitor.Log($"[HarvestCrops] Reached max_count={_maxCount}, stopping", LogLevel.Debug);
            _phase = Phase.Complete;
            return BuildResult();
        }

        // Optionally replant
        if (_replantSeed != null && !_noSeedsLeft)
        {
            return TryStartReplanting();
        }

        // No replanting — advance to next target
        AdvanceToNextTarget();
        return IsActive ? null : BuildResult();
    }

    private V2Result? TryStartReplanting()
    {
        var location = Game1.currentLocation;

        // Check if HoeDirt still exists at this tile (crop was removed by harvest, dirt remains)
        if (!location.terrainFeatures.TryGetValue(
                new Microsoft.Xna.Framework.Vector2(_currentTarget.X, _currentTarget.Y),
                out var feature) || feature is not HoeDirt dirt)
        {
            _monitor.Log($"[HarvestCrops] No HoeDirt at ({_currentTarget.X},{_currentTarget.Y}), skipping replant", LogLevel.Debug);
            AdvanceToNextTarget();
            return IsActive ? null : BuildResult();
        }

        // Check if the dirt already has a crop (regrowable crops stay after harvest)
        if (dirt.crop != null)
        {
            _monitor.Log($"[HarvestCrops] Tile ({_currentTarget.X},{_currentTarget.Y}) still has crop (regrowable), skipping replant", LogLevel.Debug);
            AdvanceToNextTarget();
            return IsActive ? null : BuildResult();
        }

        // Equip the seed
        var equipResult = _executor.EquipItemByName(_replantSeed!);
        if (!equipResult.Success)
        {
            _monitor.Log($"[HarvestCrops] No '{_replantSeed}' seeds left, stopping replanting", LogLevel.Debug);
            _noSeedsLeft = true;
            _stoppedReason = "no_seeds";
            AdvanceToNextTarget();
            return IsActive ? null : BuildResult();
        }

        // Interact with the dirt to plant the seed
        _executor.InteractWithTile(_currentTarget.X, _currentTarget.Y);
        _replantedCount++;
        _waitFrames = PostReplantWaitFrames;
        _phase = Phase.Replanting;

        _monitor.Log($"[HarvestCrops] Replanting '{_replantSeed}' at ({_currentTarget.X},{_currentTarget.Y}), replanted={_replantedCount}", LogLevel.Debug);
        return null;
    }

    private V2Result? TickReplanting()
    {
        // Wait a few frames for plant animation
        if (_waitFrames > 0)
        {
            _waitFrames--;
            return null;
        }

        // Done replanting this tile, advance
        AdvanceToNextTarget();
        return IsActive ? null : BuildResult();
    }

    // ── Helpers ──────────────────────────────────────────────────────

    /// <summary>
    /// Dequeue the next target and start navigation. If no more targets, transition to Complete.
    /// </summary>
    private void AdvanceToNextTarget()
    {
        // Check location hasn't changed (warp detection)
        if (!string.Equals(Game1.currentLocation?.NameOrUniqueName, _startLocation, StringComparison.OrdinalIgnoreCase))
        {
            _stoppedReason = "location_changed";
            _phase = Phase.Complete;
            return;
        }

        while (_targets.Count > 0)
        {
            _currentTarget = _targets.Dequeue();

            // Navigate to ADJACENT tile, not onto the crop (CRITICAL-3 fix)
            var adjacent = CompoundUtils.FindAdjacentWalkable(Game1.currentLocation!, _currentTarget);
            if (adjacent == null)
            {
                _monitor.Log($"[HarvestCrops] No walkable tile adjacent to ({_currentTarget.X},{_currentTarget.Y}), skipping", LogLevel.Debug);
                _skippedCount++;
                continue;
            }
            _standTile = adjacent.Value;

            bool navOk = _executor.NavigateTo(_standTile);
            if (navOk)
            {
                _phase = Phase.NavigatingToTile;
                _monitor.Log($"[HarvestCrops] Navigating to ({_standTile.X},{_standTile.Y}) for crop ({_currentTarget.X},{_currentTarget.Y}), remaining={_targets.Count}", LogLevel.Debug);
                return;
            }

            // No path to this tile — skip it
            _monitor.Log($"[HarvestCrops] No path to ({_standTile.X},{_standTile.Y}), skipping", LogLevel.Debug);
            _skippedCount++;
        }

        // No more targets
        _stoppedReason = "all_done";
        _phase = Phase.Complete;
        _monitor.Log($"[HarvestCrops] All targets processed", LogLevel.Debug);
    }

    /// <summary>
    /// Check if a tile still has a harvestable crop.
    /// </summary>
    private static bool IsStillHarvestable(GameLocation location, Point tile)
    {
        if (!location.terrainFeatures.TryGetValue(
                new Microsoft.Xna.Framework.Vector2(tile.X, tile.Y),
                out var feature))
            return false;

        if (feature is not HoeDirt dirt || dirt.crop == null)
            return false;

        // Use the game engine's readyForHarvest() — consistent with FindHarvestableCrops
        return dirt.readyForHarvest();
    }

    /// <summary>
    /// Build the final V2Result with harvest statistics.
    /// </summary>
    private V2Result BuildResult()
    {
        _phase = Phase.Inactive;
        var location = Game1.currentLocation;

        var data = new Dictionary<string, object>
        {
            ["harvested"] = _harvestedCount,
            ["replanted"] = _replantedCount,
            ["skipped"] = _skippedCount,
            ["stopped_reason"] = _stoppedReason,
            ["location"] = location?.NameOrUniqueName ?? "Unknown"
        };

        var result = ResultBuilder.Success(_seq, _actionId, data);

        if (_harvestedCount > 0)
            result.WithObservation($"Harvested {_harvestedCount} crops in {location?.NameOrUniqueName ?? "Unknown"}.");
        if (_replantedCount > 0)
            result.WithObservation($"Replanted {_replantedCount} tiles with {_replantSeed}.");
        if (_skippedCount > 0)
            result.WithObservation($"Skipped {_skippedCount} tiles (no path or already harvested).");
        if (_stoppedReason == "inventory_full")
            result.WithObservation("Stopped early: inventory is full.");
        if (_stoppedReason == "no_seeds")
            result.WithObservation($"Stopped replanting: no '{_replantSeed}' seeds remaining.");

        _monitor.Log($"[HarvestCrops] Complete: harvested={_harvestedCount}, replanted={_replantedCount}, skipped={_skippedCount}, reason={_stoppedReason}", LogLevel.Info);
        return result;
    }
}
