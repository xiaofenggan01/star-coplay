using System;
using System.Collections.Generic;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Tools;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// Compound handler that mines all stone rocks in the current location.
/// Only handles ordinary stones from location.objects (Name == "Stone").
/// ResourceClumps (large boulders, meteorites) are not handled.
///
/// Farm stones are always 1 hit with any pickaxe, so the state machine
/// uses pre-calculated strike count (1) — no need to poll after each strike.
///
/// State machine: Scanning → NavigatingToTile → FacingTile → Striking → WaitingAnimation → Scanning
/// </summary>
public class MineRocksHandler : ICompoundHandler
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
    private int _maxCount = 20;

    // ── Working state ──────────────────────────────────────────────
    private Queue<Point> _targets = new();
    private Point _currentTarget;
    private int _minedCount;
    private int _skippedCount;
    private int _totalStrikes;
    private int _animWaitTicks;

    private Point _standTile;
    private string _startLocation = "";

    // ── Pre-calculated strike count ──────────────────────────────
    private int _strikesPerRock;     // always 1 for farm stones
    private int _currentRockStrikes; // strikes done on current target

    // ── Debris collection ────────────────────────────────────────
    private int _collectAttempts;
    private const int MaxCollectAttempts = 3;

    // ── Constants ──────────────────────────────────────────────────
    private const int AnimationTimeout = 120;
    private const float StaminaThreshold = 20f;

    /// <inheritdoc />
    public bool IsActive => _phase != Phase.Inactive;

    public MineRocksHandler(ActionExecutor executor, IMonitor monitor)
    {
        _executor = executor;
        _monitor = monitor;
    }

    /// <inheritdoc />
    public V2Result? Start(V2Command cmd)
    {
        _seq = cmd.Seq;
        _actionId = cmd.ActionId;

        if (CompoundUtils.TryGetInt(cmd.Params, "max_count", out int mc))
            _maxCount = mc;
        else
            _maxCount = 20;

        // Reset
        _minedCount = 0;
        _skippedCount = 0;
        _totalStrikes = 0;
        _animWaitTicks = 0;
        _currentRockStrikes = 0;

        // Pre-calculate strikes (farm stones = 1)
        _strikesPerRock = CompoundUtils.GetFarmRockStrikesNeeded();

        // Validate: player has a pickaxe
        var pickaxe = CompoundUtils.FindPickaxe(Game1.player);
        if (pickaxe == null)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.ItemNotFound,
                "No pickaxe found in inventory");
        }

        if (Game1.player.Stamina < StaminaThreshold)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.OutOfEnergy,
                $"Stamina too low to mine rocks: {Game1.player.Stamina:F0} < {StaminaThreshold}");
        }

        var location = Game1.currentLocation;
        if (location == null)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.ExecutionFailed, "No active location");
        }
        _startLocation = location.NameOrUniqueName;

        var rocks = CompoundUtils.FindMineableRocks(location);
        if (rocks.Count == 0)
        {
            return ResultBuilder.Success(_seq, _actionId, new Dictionary<string, object>
            {
                ["mined"] = 0,
                ["skipped"] = 0,
                ["total_strikes"] = 0,
                ["stopped_reason"] = "all_done",
                ["location"] = location.NameOrUniqueName,
            }).WithObservation($"No mineable rocks found (current location: {location.NameOrUniqueName})");
        }

        var playerTile = Game1.player.TilePoint;
        _targets = CompoundUtils.GreedyNearestSort(playerTile, rocks);

        _monitor.Log($"[MineRocks] Starting: {_targets.Count} rocks found, max={_maxCount}, " +
            $"Stamina={Game1.player.Stamina:F0}, PickaxeLevel={pickaxe.UpgradeLevel}, strikesPerRock={_strikesPerRock}", LogLevel.Info);

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
        _monitor.Log($"[MineRocks] Cancelled: mined={_minedCount}, skipped={_skippedCount}, strikes={_totalStrikes}", LogLevel.Debug);
        _executor.CancelCurrent();
        var result = BuildResult("cancelled");
        Reset();
        return result;
    }

    // ── Phase: Scanning ────────────────────────────────────────────

    private V2Result? TickScanning()
    {
        if (_minedCount >= _maxCount)
            return Complete("max_count");

        if (Game1.player.Stamina < StaminaThreshold)
            return Complete("stamina_low");

        if (_targets.Count == 0)
            return Complete("all_done");

        _currentTarget = _targets.Dequeue();

        if (!string.Equals(Game1.currentLocation?.NameOrUniqueName, _startLocation, StringComparison.OrdinalIgnoreCase))
            return Complete("location_changed");

        if (!IsRockStillPresent(Game1.currentLocation, _currentTarget))
        {
            _skippedCount++;
            return null; // Stay in Scanning
        }

        _currentRockStrikes = 0;

        var adjacent = CompoundUtils.FindAdjacentWalkable(Game1.currentLocation!, _currentTarget);
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
            _skippedCount++;
            _phase = Phase.Scanning;
            return null;
        }

        // Re-verify rock still exists
        if (!IsRockStillPresent(Game1.currentLocation, _currentTarget))
        {
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

        if (Game1.player.CurrentTool is not Pickaxe)
        {
            var pick = CompoundUtils.FindToolByCategory(Game1.player, "pickaxe");
            var equipResult = pick != null ? _executor.EquipItemByName(pick.Name) : new EquipResult(false, -1, null, false, -1, -1);
            if (!equipResult.Success)
                return Complete("no_pickaxe");
        }

        _phase = Phase.Striking;
        return null;
    }

    // ── Phase: Striking ────────────────────────────────────────────

    private V2Result? TickStriking()
    {
        if (Game1.player.CurrentTool is not Pickaxe)
        {
            var pick = CompoundUtils.FindToolByCategory(Game1.player, "pickaxe");
            var equipResult = pick != null ? _executor.EquipItemByName(pick.Name) : new EquipResult(false, -1, null, false, -1, -1);
            if (!equipResult.Success)
                return Complete("no_pickaxe");
        }

        if (!_executor.UseToolAt(_currentTarget.X, _currentTarget.Y))
        {
            _skippedCount++;
            _phase = Phase.Scanning;
            return null;
        }

        _totalStrikes++;
        _currentRockStrikes++;
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
            _monitor.Log("[MineRocks] Animation timeout, force-resetting", LogLevel.Warn);
            CompoundUtils.ForceResetToolState(Game1.player);
            return OnStrikeComplete();
        }

        return null;
    }

    private V2Result? OnStrikeComplete()
    {
        if (_currentRockStrikes >= _strikesPerRock)
        {
            // Rock should be broken
            _minedCount++;
            _monitor.Log($"[MineRocks] Rock ({_currentTarget.X},{_currentTarget.Y}) mined after {_currentRockStrikes} strike(s), total={_minedCount}", LogLevel.Debug);

            if (Game1.player.Stamina < StaminaThreshold)
                return Complete("stamina_low");
            if (_minedCount >= _maxCount)
                return Complete("max_count");

            // Check for nearby debris to collect before moving to next rock
            _collectAttempts = 0;
            var debrisTile = CompoundUtils.FindNearestDebris(Game1.currentLocation!, _currentTarget);
            if (debrisTile != null)
            {
                _monitor.Log($"[MineRocks] Debris found near ({debrisTile.Value.X},{debrisTile.Value.Y}), collecting", LogLevel.Debug);
                _executor.NavigateTo(debrisTile.Value);
                _phase = Phase.CollectDebris;
                return null;
            }

            _phase = Phase.Scanning;
            return null;
        }

        // More strikes needed (rare for farm stones, but handles edge cases)
        if (Game1.player.Stamina < StaminaThreshold)
            return Complete("stamina_low");

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
            _monitor.Log($"[MineRocks] More debris at ({moreDebris.Value.X},{moreDebris.Value.Y}), attempt {_collectAttempts}/{MaxCollectAttempts}", LogLevel.Debug);
            _executor.NavigateTo(moreDebris.Value);
            return null;
        }

        // Done collecting (or max attempts reached)
        _phase = Phase.Scanning;
        return null;
    }

    // ── Helpers ────────────────────────────────────────────────────

    private static bool IsRockStillPresent(GameLocation? location, Point tile)
    {
        if (location == null) return false;
        var key = new Vector2(tile.X, tile.Y);
        return location.objects.TryGetValue(key, out var obj)
            && obj is StardewValley.Object sObj
            && sObj.Name == "Stone";
    }

    // ── Completion helpers ─────────────────────────────────────────

    private V2Result Complete(string stoppedReason)
    {
        _monitor.Log($"[MineRocks] Complete: mined={_minedCount}, skipped={_skippedCount}, strikes={_totalStrikes}, reason={stoppedReason}", LogLevel.Info);
        var result = BuildResult(stoppedReason);
        Reset();
        return result;
    }

    private V2Result BuildResult(string stoppedReason)
    {
        var data = new Dictionary<string, object>
        {
            ["mined"] = _minedCount,
            ["skipped"] = _skippedCount,
            ["total_strikes"] = _totalStrikes,
            ["stopped_reason"] = stoppedReason,
            ["location"] = Game1.currentLocation?.NameOrUniqueName ?? "Unknown",
        };

        var result = ResultBuilder.Success(_seq, _actionId, data);

        if (_totalStrikes > 0)
            result.WithSideEffect("stamina", $"-{_totalStrikes * 2} (approx)");

        return result;
    }

    private void Reset()
    {
        _phase = Phase.Inactive;
        _targets.Clear();
        _currentTarget = default;
        _standTile = default;
        _startLocation = "";
        _minedCount = 0;
        _skippedCount = 0;
        _totalStrikes = 0;
        _animWaitTicks = 0;
        _currentRockStrikes = 0;
        _collectAttempts = 0;
    }
}
