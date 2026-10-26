using System;
using System.Collections.Generic;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Tools;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// Compound handler that waters all unwatered crops in the current location.
/// Manages navigation, tool use, and refill cycles across multiple game frames.
/// </summary>
public class WaterCropsHandler : ICompoundHandler
{
    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;

    // ── State machine ──────────────────────────────────────────────
    private enum Phase
    {
        Inactive,
        Scanning,
        NavigatingToTile,
        FacingTile,
        Watering,
        WaitingAnimation,
        NavigatingToWater,
        FacingWater,
        Refilling,
        WaitingRefillAnim,
    }

    private Phase _phase = Phase.Inactive;

    // ── Command context ────────────────────────────────────────────
    private int _seq;
    private string _actionId = "";
    private int _maxCount = 50;

    // ── Working state ──────────────────────────────────────────────
    private Queue<Point> _targets = new();
    private Point _currentTarget;
    private int _wateredCount;
    private int _skippedCount;
    private int _refillCount;
    private int _animWaitTicks;
    private int _maxRefills = 3;

    private Point _standTile;          // Adjacent tile to stand on while working
    private string _startLocation = ""; // Location name at start (detect warp)

    // ── Refill sub-flow ────────────────────────────────────────────
    private Point _waterTile;
    private Point _waterStandTile;

    // ── Constants ──────────────────────────────────────────────────
    private const int AnimationTimeout = 120; // ~2 seconds at 60fps

    /// <inheritdoc />
    public bool IsActive => _phase != Phase.Inactive;

    /// <summary>
    /// Initialize and start the water_crops compound action.
    /// Returns V2Result immediately if validation fails.
    /// Returns null if async execution has started.
    /// </summary>
    public V2Result? Start(V2Command cmd)
    {
        _seq = cmd.Seq;
        _actionId = cmd.ActionId;

        // Parse parameters
        if (CompoundUtils.TryGetInt(cmd.Params, "max_count", out int mc))
            _maxCount = mc;
        else
            _maxCount = 50;

        // Reset counters
        _wateredCount = 0;
        _skippedCount = 0;
        _refillCount = 0;
        _animWaitTicks = 0;

        // Validate: player has a watering can
        var can = CompoundUtils.FindWateringCan(Game1.player);
        if (can == null)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.ItemNotFound,
                "No watering can found in inventory");
        }

        // Find unwatered crops
        var location = Game1.currentLocation;
        if (location == null)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.ExecutionFailed, "No active location");
        }
        _startLocation = location.NameOrUniqueName;
        var unwatered = CompoundUtils.FindUnwateredCrops(location);
        if (unwatered.Count == 0)
        {
            return ResultBuilder.Success(_seq, _actionId, new Dictionary<string, object>
            {
                ["watered"] = 0,
                ["skipped"] = 0,
                ["refilled"] = 0,
                ["stopped_reason"] = "all_done",
                ["location"] = location.NameOrUniqueName,
            }).WithObservation($"No unwatered crops found (current location: {location.NameOrUniqueName})");
        }

        // Sort by greedy nearest neighbor
        var playerTile = Game1.player.TilePoint;
        _targets = CompoundUtils.GreedyNearestSort(playerTile, unwatered);

        _monitor.Log($"[WaterCrops] Starting: {_targets.Count} unwatered crops, max={_maxCount}, " +
            $"WaterLeft={can.WaterLeft}/{can.waterCanMax}, UpgradeLevel={can.UpgradeLevel}", LogLevel.Info);

        // If watering can is empty at start, try to refill first (same as V1)
        if (can.WaterLeft <= 0)
        {
            _monitor.Log("[WaterCrops] Watering can is empty, attempting refill before starting", LogLevel.Info);
            _phase = Phase.Scanning; // Set phase so TryStartRefill can transition
            var refillResult = TryStartRefill();
            if (refillResult != null)
            {
                // TryStartRefill returned a result = refill failed
                return refillResult;
            }
            // refillResult == null means refill navigation started, will continue via Tick()
            return null;
        }

        // Enter scanning phase (will dequeue first target on next Tick)
        _phase = Phase.Scanning;
        return null;
    }

    /// <summary>
    /// Advance the state machine by one frame.
    /// Returns V2Result when complete, null while in progress.
    /// </summary>
    public V2Result? Tick()
    {
        switch (_phase)
        {
            case Phase.Scanning:
                return TickScanning();
            case Phase.NavigatingToTile:
                return TickNavigatingToTile();
            case Phase.FacingTile:
                return TickFacingTile();
            case Phase.Watering:
                return TickWatering();
            case Phase.WaitingAnimation:
                return TickWaitingAnimation();
            case Phase.NavigatingToWater:
                return TickNavigatingToWater();
            case Phase.FacingWater:
                return TickFacingWater();
            case Phase.Refilling:
                return TickRefilling();
            case Phase.WaitingRefillAnim:
                return TickWaitingRefillAnim();
            default:
                return null;
        }
    }

    /// <inheritdoc />
    public V2Result Cancel()
    {
        _monitor.Log($"[WaterCrops] Cancelled: watered={_wateredCount}, skipped={_skippedCount}", LogLevel.Debug);
        _executor.CancelCurrent();
        var result = BuildResult("cancelled");
        Reset();
        return result;
    }

    /// <summary>
    /// Create a new WaterCropsHandler.
    /// </summary>
    /// <param name="executor">ActionExecutor for navigation and tool use.</param>
    /// <param name="monitor">SMAPI logger.</param>
    public WaterCropsHandler(ActionExecutor executor, IMonitor monitor)
    {
        _executor = executor;
        _monitor = monitor;
    }

    // ── Phase: Scanning ────────────────────────────────────────────

    private V2Result? TickScanning()
    {
        // Dequeue next target
        if (_targets.Count == 0)
        {
            return Complete("all_done");
        }

        _currentTarget = _targets.Dequeue();

        // Check location hasn't changed (HIGH-6: warp detection)
        if (!string.Equals(Game1.currentLocation?.NameOrUniqueName, _startLocation, StringComparison.OrdinalIgnoreCase))
            return Complete("location_changed");

        // Navigate to an ADJACENT tile, not onto the crop (CRITICAL-3 fix)
        var adjacent = CompoundUtils.FindAdjacentWalkable(Game1.currentLocation!, _currentTarget);
        if (adjacent == null)
        {
            _monitor.Log($"[WaterCrops] No walkable tile adjacent to ({_currentTarget.X},{_currentTarget.Y}), skipping", LogLevel.Debug);
            _skippedCount++;
            return null; // Stay in Scanning to try next
        }
        _standTile = adjacent.Value;

        if (!_executor.NavigateTo(_standTile))
        {
            _monitor.Log($"[WaterCrops] Cannot path to stand tile ({_standTile.X},{_standTile.Y}), skipping", LogLevel.Debug);
            _skippedCount++;
            return null;
        }

        _phase = Phase.NavigatingToTile;
        _monitor.Log($"[WaterCrops] Navigating to ({_standTile.X},{_standTile.Y}) for crop ({_currentTarget.X},{_currentTarget.Y})", LogLevel.Debug);
        return null;
    }

    // ── Phase: NavigatingToTile ────────────────────────────────────

    private V2Result? TickNavigatingToTile()
    {
        if (_executor.IsBusy)
            return null;

        // Verify navigation actually reached near the target (HIGH-5 fix)
        var playerTile = Game1.player.TilePoint;
        int dist = System.Math.Abs(playerTile.X - _currentTarget.X)
                 + System.Math.Abs(playerTile.Y - _currentTarget.Y);
        if (dist > 3)
        {
            _monitor.Log($"[WaterCrops] Nav ended too far from ({_currentTarget.X},{_currentTarget.Y}), dist={dist}, skipping", LogLevel.Debug);
            _skippedCount++;
            _phase = Phase.Scanning;
            return null;
        }

        // Navigation complete — face the target tile
        int dir = CompoundUtils.GetFacingDirection(playerTile, _currentTarget);
        Game1.player.faceDirection(dir);

        _phase = Phase.FacingTile;
        _animWaitTicks = 0;
        return null;
    }

    // ── Phase: FacingTile (1-frame pause) ──────────────────────────

    private V2Result? TickFacingTile()
    {
        // Wait 1 frame for facing to settle
        _animWaitTicks++;
        if (_animWaitTicks < 1)
            return null;

        // Equip watering can
        if (Game1.player.CurrentTool is not WateringCan)
        {
            var wcan = CompoundUtils.FindToolByCategory(Game1.player, "watering_can");
            var equipResult = wcan != null ? _executor.EquipItemByName(wcan.Name) : new EquipResult(false, -1, null, false, -1, -1);
            if (!equipResult.Success)
            {
                _monitor.Log("[WaterCrops] Failed to equip watering can", LogLevel.Warn);
                return Complete("equip_failed");
            }
        }

        // Use tool at target
        if (!_executor.UseToolAt(_currentTarget.X, _currentTarget.Y))
        {
            _monitor.Log($"[WaterCrops] UseToolAt failed at ({_currentTarget.X},{_currentTarget.Y}), skipping", LogLevel.Debug);
            _skippedCount++;
            _phase = Phase.Scanning;
            return null;
        }

        _phase = Phase.WaitingAnimation;
        _animWaitTicks = 0;
        return null;
    }

    // ── Phase: Watering (unused, merged into FacingTile) ───────────
    // Kept for state machine completeness; FacingTile transitions directly to WaitingAnimation.

    private V2Result? TickWatering()
    {
        // This phase is not used in current flow; redirect to scanning
        _phase = Phase.Scanning;
        return null;
    }

    // ── Phase: WaitingAnimation ────────────────────────────────────

    private V2Result? TickWaitingAnimation()
    {
        _animWaitTicks++;

        // Check if animation completed
        if (!Game1.player.UsingTool && Game1.player.canMove)
        {
            return OnWateringComplete();
        }

        // Timeout guard
        if (_animWaitTicks >= AnimationTimeout)
        {
            _monitor.Log("[WaterCrops] Animation timeout, force-resetting tool state", LogLevel.Warn);
            CompoundUtils.ForceResetToolState(Game1.player);
            return OnWateringComplete();
        }

        return null;
    }

    private V2Result? OnWateringComplete()
    {
        _wateredCount++;
        var canAfter = CompoundUtils.FindWateringCan(Game1.player);
        _monitor.Log($"[WaterCrops] Watered ({_currentTarget.X},{_currentTarget.Y}), total={_wateredCount}, " +
            $"WaterLeft={canAfter?.WaterLeft ?? -1}/{canAfter?.waterCanMax ?? 0}", LogLevel.Debug);

        // Check continue conditions
        if (Game1.player.Stamina < 10f)
        {
            return Complete("stamina_low");
        }

        if (_wateredCount >= _maxCount)
        {
            return Complete("max_count");
        }

        // Check water remaining
        var can = CompoundUtils.FindWateringCan(Game1.player);
        if (can != null && can.WaterLeft <= 0)
        {
            return TryStartRefill();
        }

        if (_targets.Count == 0)
        {
            return Complete("all_done");
        }

        // Continue to next tile
        _phase = Phase.Scanning;
        return null;
    }

    // ── Refill sub-flow ────────────────────────────────────────────

    private V2Result? TryStartRefill()
    {
        if (_refillCount >= _maxRefills)
        {
            _monitor.Log("[WaterCrops] Max refills reached", LogLevel.Debug);
            return Complete("no_water_source");
        }

        var location = Game1.currentLocation;
        if (location == null)
            return Complete("no_water_source");

        _monitor.Log($"[WaterCrops] Searching reachable water source on {location.NameOrUniqueName}", LogLevel.Info);

        // Step 1: Try NavigateTo (walk to water) — may fail if path is too long or blocked
        var result = CompoundUtils.FindReachableWaterSource(location, _executor, maxCandidates: 10);
        if (result != null)
        {
            _waterTile = result.Value.waterTile;
            _waterStandTile = result.Value.standTile;
            _monitor.Log($"[WaterCrops] Refilling (walk): navigating to ({_waterStandTile.X},{_waterStandTile.Y}) near water ({_waterTile.X},{_waterTile.Y})", LogLevel.Info);
            // Navigation already started by FindReachableWaterSource
            _phase = Phase.NavigatingToWater;
            return null;
        }

        _monitor.Log(
            $"[WaterCrops] No reachable water source on {location.NameOrUniqueName}; " +
            "teleport fallback is disabled",
            LogLevel.Warn);
        return Complete("no_water_source");
    }

    // ── Phase: NavigatingToWater ───────────────────────────────────

    private V2Result? TickNavigatingToWater()
    {
        if (_executor.IsBusy)
            return null;

        // Face the water tile
        var playerTile = Game1.player.TilePoint;
        int dir = CompoundUtils.GetFacingDirection(playerTile, _waterTile);
        Game1.player.faceDirection(dir);

        _phase = Phase.FacingWater;
        _animWaitTicks = 0;
        return null;
    }

    // ── Phase: FacingWater (1-frame pause) ─────────────────────────

    private V2Result? TickFacingWater()
    {
        _animWaitTicks++;
        if (_animWaitTicks < 1)
            return null;

        // Equip watering can if needed
        if (Game1.player.CurrentTool is not WateringCan)
        {
            var wcan = CompoundUtils.FindToolByCategory(Game1.player, "watering_can");
            var equipResult = wcan != null ? _executor.EquipItemByName(wcan.Name) : new EquipResult(false, -1, null, false, -1, -1);
            if (!equipResult.Success)
            {
                _monitor.Log("[WaterCrops] Failed to equip watering can for refill", LogLevel.Warn);
                return Complete("equip_failed");
            }
        }

        // Use tool at water tile to refill
        if (!_executor.UseToolAt(_waterTile.X, _waterTile.Y))
        {
            _monitor.Log("[WaterCrops] UseToolAt water tile failed", LogLevel.Warn);
            return Complete("no_water_source");
        }

        _phase = Phase.WaitingRefillAnim;
        _animWaitTicks = 0;
        return null;
    }

    // ── Phase: Refilling (unused, merged into FacingWater) ─────────

    private V2Result? TickRefilling()
    {
        _phase = Phase.Scanning;
        return null;
    }

    // ── Phase: WaitingRefillAnim ───────────────────────────────────

    private V2Result? TickWaitingRefillAnim()
    {
        _animWaitTicks++;

        if (!Game1.player.UsingTool && Game1.player.canMove)
        {
            _refillCount++;
            _monitor.Log($"[WaterCrops] Refill #{_refillCount} complete", LogLevel.Debug);

            if (_targets.Count == 0)
                return Complete("all_done");

            // Resume watering
            _phase = Phase.Scanning;
            return null;
        }

        if (_animWaitTicks >= AnimationTimeout)
        {
            _monitor.Log("[WaterCrops] Refill animation timeout, force-resetting", LogLevel.Warn);
            CompoundUtils.ForceResetToolState(Game1.player);
            _refillCount++;

            if (_targets.Count == 0)
                return Complete("all_done");

            _phase = Phase.Scanning;
            return null;
        }

        return null;
    }

    // ── Completion helpers ─────────────────────────────────────────

    private V2Result Complete(string stoppedReason)
    {
        _monitor.Log($"[WaterCrops] Complete: watered={_wateredCount}, skipped={_skippedCount}, refilled={_refillCount}, reason={stoppedReason}", LogLevel.Debug);
        var result = BuildResult(stoppedReason);
        Reset();
        return result;
    }

    private V2Result BuildResult(string stoppedReason)
    {
        var data = new Dictionary<string, object>
        {
            ["watered"] = _wateredCount,
            ["skipped"] = _skippedCount,
            ["refilled"] = _refillCount,
            ["stopped_reason"] = stoppedReason,
            ["location"] = Game1.currentLocation?.NameOrUniqueName ?? "Unknown",
        };

        var result = ResultBuilder.Success(_seq, _actionId, data);

        if (_wateredCount > 0)
            result.WithSideEffect("stamina", $"-{_wateredCount * 2} (approx)");

        return result;
    }

    private void Reset()
    {
        _phase = Phase.Inactive;
        _targets.Clear();
        _currentTarget = default;
        _standTile = default;
        _startLocation = "";
        _waterTile = default;
        _waterStandTile = default;
        _wateredCount = 0;
        _skippedCount = 0;
        _refillCount = 0;
        _animWaitTicks = 0;
    }
}
