using System;
using System.Collections.Generic;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.TerrainFeatures;
using StardewValley.Tools;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// Compound handler that chops all mature trees (and optionally stumps) in the current location.
/// Uses pre-calculated strike counts based on axe upgrade level (wiki lookup table)
/// instead of polling whether the tree still exists after each strike.
///
/// State machine: Scanning → NavigatingToTile → FacingTile → Striking → WaitingAnimation → (loop N strikes) → Scanning
/// </summary>
public class ChopTreesHandler : ICompoundHandler
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
        Striking,
        WaitingAnimation,
        CollectDebris,
    }

    private Phase _phase = Phase.Inactive;

    // ── Command context ────────────────────────────────────────────
    private int _seq;
    private string _actionId = "";
    private int _maxCount = 10;
    private bool _includeStumps;

    // ── Working state ──────────────────────────────────────────────
    private Queue<Point> _targets = new();
    private Point _currentTarget;
    private int _choppedCount;
    private int _skippedCount;
    private int _totalStrikes;
    private int _animWaitTicks;

    private Point _standTile;
    private string _startLocation = "";

    // ── Pre-calculated strike counts ─────────────────────────────
    private int _strikesPerTree;    // strikes needed for a full mature tree
    private int _strikesPerStump;   // strikes needed for a stump
    private int _currentTreeStrikes; // strikes done on current target
    private bool _currentIsStump;    // whether current target is a stump
    private int _axeLevel;           // cached axe upgrade level

    // ── Debris collection ────────────────────────────────────────
    private int _collectAttempts;
    private const int MaxCollectAttempts = 3;

    // ── Constants ──────────────────────────────────────────────────
    private const int AnimationTimeout = 120;
    private const float StaminaThreshold = 20f;

    /// <inheritdoc />
    public bool IsActive => _phase != Phase.Inactive;

    public ChopTreesHandler(ActionExecutor executor, IMonitor monitor)
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
        if (CompoundUtils.TryGetInt(cmd.Params, "max_count", out int mc))
            _maxCount = mc;
        else
            _maxCount = 10;

        if (CompoundUtils.TryGetBool(cmd.Params, "include_stumps", out bool includeStumps))
            _includeStumps = includeStumps;
        else
            _includeStumps = false;

        // Reset counters
        _choppedCount = 0;
        _skippedCount = 0;
        _totalStrikes = 0;
        _animWaitTicks = 0;
        _currentTreeStrikes = 0;
        _currentIsStump = false;

        // Validate: player has an axe
        var axe = CompoundUtils.FindAxe(Game1.player);
        if (axe == null)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.ItemNotFound,
                "No axe found in inventory");
        }
        _axeLevel = axe.UpgradeLevel;

        // Pre-calculate strikes needed (wiki lookup table)
        _strikesPerTree = CompoundUtils.GetTreeStrikesNeeded(_axeLevel, isStump: false);
        _strikesPerStump = CompoundUtils.GetTreeStrikesNeeded(_axeLevel, isStump: true);

        // Validate: player has enough stamina for at least one tree
        if (Game1.player.Stamina < StaminaThreshold)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.OutOfEnergy,
                $"Stamina too low to chop trees: {Game1.player.Stamina:F0} < {StaminaThreshold}");
        }

        // Find choppable trees
        var location = Game1.currentLocation;
        if (location == null)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.ExecutionFailed, "No active location");
        }
        _startLocation = location.NameOrUniqueName;

        var trees = CompoundUtils.FindChoppableTrees(location, _includeStumps);
        if (trees.Count == 0)
        {
            return ResultBuilder.Success(_seq, _actionId, new Dictionary<string, object>
            {
                ["chopped"] = 0,
                ["skipped"] = 0,
                ["total_strikes"] = 0,
                ["stopped_reason"] = "all_done",
                ["location"] = location.NameOrUniqueName,
            }).WithObservation($"No choppable trees found (current location: {location.NameOrUniqueName})");
        }

        // Sort by greedy nearest neighbor
        var playerTile = Game1.player.TilePoint;
        _targets = CompoundUtils.GreedyNearestSort(playerTile, trees);

        _monitor.Log($"[ChopTrees] Starting: {_targets.Count} trees found, max={_maxCount}, " +
            $"AxeLevel={_axeLevel}, strikesPerTree={_strikesPerTree}, strikesPerStump={_strikesPerStump}", LogLevel.Info);

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
                return TickNavigatingToTile();
            case Phase.FacingTile:
                return TickFacingTile();
            case Phase.Striking:
                return TickStriking();
            case Phase.WaitingAnimation:
                return TickWaitingAnimation();
            case Phase.CollectDebris:
                return TickCollectDebris();
            default:
                return null;
        }
    }

    /// <inheritdoc />
    public V2Result Cancel()
    {
        _monitor.Log($"[ChopTrees] Cancelled: chopped={_choppedCount}, skipped={_skippedCount}, strikes={_totalStrikes}", LogLevel.Debug);
        _executor.CancelCurrent();
        var result = BuildResult("cancelled");
        Reset();
        return result;
    }

    // ── Phase: Scanning ────────────────────────────────────────────

    private V2Result? TickScanning()
    {
        if (_choppedCount >= _maxCount)
            return Complete("max_count");

        if (Game1.player.Stamina < StaminaThreshold)
            return Complete("stamina_low");

        if (_targets.Count == 0)
            return Complete("all_done");

        _currentTarget = _targets.Dequeue();

        if (!string.Equals(Game1.currentLocation?.NameOrUniqueName, _startLocation, StringComparison.OrdinalIgnoreCase))
            return Complete("location_changed");

        // Check tree still exists and determine if it's a stump
        var location = Game1.currentLocation!;
        var key = new Vector2(_currentTarget.X, _currentTarget.Y);
        if (!location.terrainFeatures.TryGetValue(key, out var feature) || feature is not Tree tree)
        {
            _skippedCount++;
            return null; // Stay in Scanning
        }

        _currentIsStump = tree.stump.Value;
        if (!_includeStumps && _currentIsStump)
        {
            _skippedCount++;
            return null;
        }

        _currentTreeStrikes = 0; // Reset per-tree counter

        // Navigate to adjacent tile
        var adjacent = CompoundUtils.FindAdjacentWalkable(location, _currentTarget);
        if (adjacent == null)
        {
            _skippedCount++;
            return null;
        }
        _standTile = adjacent.Value;

        if (!_executor.NavigateTo(_standTile))
        {
            _skippedCount++;
            return null;
        }

        _phase = Phase.NavigatingToTile;
        _monitor.Log($"[ChopTrees] Navigating to tree ({_currentTarget.X},{_currentTarget.Y}), " +
            $"isStump={_currentIsStump}, strikesNeeded={(_currentIsStump ? _strikesPerStump : _strikesPerTree)}", LogLevel.Debug);
        return null;
    }

    // ── Phase: NavigatingToTile ────────────────────────────────────

    private V2Result? TickNavigatingToTile()
    {
        if (_executor.IsBusy)
            return null;

        var playerTile = Game1.player.TilePoint;
        int dist = System.Math.Abs(playerTile.X - _currentTarget.X)
                 + System.Math.Abs(playerTile.Y - _currentTarget.Y);
        if (dist > 3)
        {
            _monitor.Log($"[ChopTrees] Nav ended too far (dist={dist}), skipping", LogLevel.Debug);
            _skippedCount++;
            _phase = Phase.Scanning;
            return null;
        }

        // Re-verify tree still exists after navigation
        if (!IsTreeStillPresent(Game1.currentLocation, _currentTarget, _includeStumps))
        {
            _monitor.Log($"[ChopTrees] Tree vanished during navigation, skipping", LogLevel.Debug);
            _skippedCount++;
            _phase = Phase.Scanning;
            return null;
        }

        int dir = CompoundUtils.GetFacingDirection(playerTile, _currentTarget);
        Game1.player.faceDirection(dir);

        _phase = Phase.FacingTile;
        _animWaitTicks = 0;
        return null;
    }

    // ── Phase: FacingTile ──────────────────────────────────────────

    private V2Result? TickFacingTile()
    {
        if (_animWaitTicks < 1)
        {
            _animWaitTicks++;
            return null;
        }

        // Equip axe
        if (Game1.player.CurrentTool is not Axe)
        {
            var axe = CompoundUtils.FindToolByCategory(Game1.player, "axe");
            var equipResult = axe != null ? _executor.EquipItemByName(axe.Name) : new EquipResult(false, -1, null, false, -1, -1);
            if (!equipResult.Success)
                return Complete("no_axe");
        }

        _phase = Phase.Striking;
        return null;
    }

    // ── Phase: Striking ────────────────────────────────────────────

    private V2Result? TickStriking()
    {
        // Re-equip if somehow changed
        if (Game1.player.CurrentTool is not Axe)
        {
            var axe = CompoundUtils.FindToolByCategory(Game1.player, "axe");
            var equipResult = axe != null ? _executor.EquipItemByName(axe.Name) : new EquipResult(false, -1, null, false, -1, -1);
            if (!equipResult.Success)
                return Complete("no_axe");
        }

        if (!_executor.UseToolAt(_currentTarget.X, _currentTarget.Y))
        {
            _monitor.Log($"[ChopTrees] UseToolAt failed, skipping tree", LogLevel.Debug);
            _skippedCount++;
            _phase = Phase.Scanning;
            return null;
        }

        _totalStrikes++;
        _currentTreeStrikes++;
        _phase = Phase.WaitingAnimation;
        _animWaitTicks = 0;
        return null;
    }

    // ── Phase: WaitingAnimation ────────────────────────────────────

    private V2Result? TickWaitingAnimation()
    {
        _animWaitTicks++;

        if (!Game1.player.UsingTool && Game1.player.canMove)
        {
            return OnStrikeComplete();
        }

        if (_animWaitTicks >= AnimationTimeout)
        {
            _monitor.Log("[ChopTrees] Animation timeout, force-resetting", LogLevel.Warn);
            CompoundUtils.ForceResetToolState(Game1.player);
            return OnStrikeComplete();
        }

        return null;
    }

    private V2Result? OnStrikeComplete()
    {
        int needed = _currentIsStump ? _strikesPerStump : _strikesPerTree;

        if (_currentTreeStrikes >= needed)
        {
            // Tree should be down — count it
            _choppedCount++;
            _monitor.Log($"[ChopTrees] Tree ({_currentTarget.X},{_currentTarget.Y}) chopped after {_currentTreeStrikes} strikes " +
                $"(expected {needed}), total chopped={_choppedCount}", LogLevel.Debug);

            if (Game1.player.Stamina < StaminaThreshold)
                return Complete("stamina_low");
            if (_choppedCount >= _maxCount)
                return Complete("max_count");

            // Check for nearby debris to collect before moving to next tree
            _collectAttempts = 0;
            var debrisTile = CompoundUtils.FindNearestDebris(Game1.currentLocation!, _currentTarget);
            if (debrisTile != null)
            {
                _monitor.Log($"[ChopTrees] Debris found near ({debrisTile.Value.X},{debrisTile.Value.Y}), collecting", LogLevel.Debug);
                _executor.NavigateTo(debrisTile.Value);
                _phase = Phase.CollectDebris;
                return null;
            }

            _phase = Phase.Scanning;
            return null;
        }

        // More strikes needed — check stamina before continuing
        if (Game1.player.Stamina < StaminaThreshold)
            return Complete("stamina_low");

        // Continue striking (inner loop)
        _phase = Phase.Striking;
        return null;
    }

    // ── Phase: CollectDebris ─────────────────────────────────────

    private V2Result? TickCollectDebris()
    {
        if (_executor.IsBusy)
            return null;

        // Check if more debris remains nearby
        var moreDebris = CompoundUtils.FindNearestDebris(Game1.currentLocation!, _currentTarget);
        if (moreDebris != null && _collectAttempts < MaxCollectAttempts)
        {
            _collectAttempts++;
            _monitor.Log($"[ChopTrees] More debris at ({moreDebris.Value.X},{moreDebris.Value.Y}), attempt {_collectAttempts}/{MaxCollectAttempts}", LogLevel.Debug);
            _executor.NavigateTo(moreDebris.Value);
            return null;
        }

        // Done collecting (or max attempts reached)
        _phase = Phase.Scanning;
        return null;
    }

    // ── Helpers ────────────────────────────────────────────────────

    private static bool IsTreeStillPresent(GameLocation? location, Point tile, bool includeStumps)
    {
        if (location == null) return false;
        var key = new Vector2(tile.X, tile.Y);
        if (!location.terrainFeatures.TryGetValue(key, out var feature) || feature is not Tree tree)
            return false;
        if (!includeStumps && tree.stump.Value)
            return false;
        return true;
    }

    // ── Completion helpers ─────────────────────────────────────────

    private V2Result Complete(string stoppedReason)
    {
        _monitor.Log($"[ChopTrees] Complete: chopped={_choppedCount}, skipped={_skippedCount}, strikes={_totalStrikes}, reason={stoppedReason}", LogLevel.Info);
        var result = BuildResult(stoppedReason);
        Reset();
        return result;
    }

    private V2Result BuildResult(string stoppedReason)
    {
        var data = new Dictionary<string, object>
        {
            ["chopped"] = _choppedCount,
            ["skipped"] = _skippedCount,
            ["total_strikes"] = _totalStrikes,
            ["stopped_reason"] = stoppedReason,
            ["location"] = Game1.currentLocation?.NameOrUniqueName ?? "Unknown",
        };

        var result = ResultBuilder.Success(_seq, _actionId, data);

        if (_totalStrikes > 0)
            result.WithSideEffect("stamina", $"-{_totalStrikes * 3} (approx)");

        return result;
    }

    private void Reset()
    {
        _phase = Phase.Inactive;
        _targets.Clear();
        _currentTarget = default;
        _standTile = default;
        _startLocation = "";
        _choppedCount = 0;
        _skippedCount = 0;
        _totalStrikes = 0;
        _animWaitTicks = 0;
        _currentTreeStrikes = 0;
        _currentIsStump = false;
        _collectAttempts = 0;
    }
}
