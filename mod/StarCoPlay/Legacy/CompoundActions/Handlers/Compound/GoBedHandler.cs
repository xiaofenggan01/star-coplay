using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Locations;
using StardewValley.Menus;
using StardewValley.Objects;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// Compound handler: go_bed — warp home, position on bed, trigger sleep, confirm dialog.
/// State machine: WaitingForWarp → Sleeping → WaitingForDialog → Complete
///
/// Follows V1's proven approach: warp directly to bed area, position player on bed,
/// trigger checkForAction, then confirm the sleep dialog. No pathfinding needed.
///
/// Supports both host (FarmHouse) and farmhand (Cabin) via Game1.player.homeLocation.Value.
/// </summary>
public class GoBedHandler : ICompoundHandler, ICompoundDebugState
{
    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;

    // ── State machine (simplified, matching V1's proven flow) ─────
    private enum Phase
    {
        Inactive,
        WaitingForWarp,
        Sleeping,
        WaitingForDialog,
    }

    private Phase _phase = Phase.Inactive;

    // ── Command context ────────────────────────────────────────────
    private int _seq;
    private string _actionId = "";

    // ── Working state ──────────────────────────────────────────────
    private string _homeLocationName = "";
    private Point _bedTile;
    private BedFurniture? _bed;
    private int _waitTicks;

    // ── Constants ──────────────────────────────────────────────────
    private const int MaxWarpWaitFrames = 300;   // ~5 seconds
    private const int MaxDialogWaitFrames = 120; // ~2 seconds

    /// <inheritdoc />
    public bool IsActive => _phase != Phase.Inactive;

    public string GetDebugState()
    {
        var currentMap = Game1.currentLocation?.NameOrUniqueName ?? "Unknown";
        var menu = Game1.activeClickableMenu?.GetType().Name ?? "none";
        var bed = _bed != null ? $"{_bed.Name}@{_bedTile.X},{_bedTile.Y}" : "none";
        return $"phase={_phase},waitTicks={_waitTicks},home={_homeLocationName},current={currentMap},bed={bed},menu={menu}";
    }

    public GoBedHandler(ActionExecutor executor, IMonitor monitor)
    {
        _executor = executor;
        _monitor = monitor;
    }

    /// <inheritdoc />
    public V2Result? Start(V2Command cmd)
    {
        _seq = cmd.Seq;
        _actionId = cmd.ActionId;
        _waitTicks = 0;

        // Determine home location
        var player = Game1.player;
        if (player == null)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.ExecutionFailed, "Player is null");
        }

        _homeLocationName = player.homeLocation.Value ?? "FarmHouse";
        _monitor.Log($"[GoBed] Home location: {_homeLocationName}, current: {Game1.currentLocation?.NameOrUniqueName}", LogLevel.Info);

        // If already at home, skip warp and go directly to sleep phase
        string currentMap = Game1.currentLocation?.NameOrUniqueName ?? "";
        if (IsHomeLocation(currentMap))
        {
            _monitor.Log("[GoBed] Already at home, skipping warp", LogLevel.Debug);
            _phase = Phase.Sleeping;
            return null;
        }

        // Warp to home — use default bed-area coordinates (V1: 22,5 for FarmHouse)
        // The exact warp tile doesn't matter much since we'll reposition onto the bed anyway
        _monitor.Log($"[GoBed] Warping to {_homeLocationName}", LogLevel.Info);
        bool ok = _executor.WarpTo(_homeLocationName, 22, 5);
        if (!ok)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"Failed to warp to {_homeLocationName}");
        }

        _phase = Phase.WaitingForWarp;
        _waitTicks = 0;
        return null;
    }

    /// <inheritdoc />
    public V2Result? Tick()
    {
        switch (_phase)
        {
            case Phase.WaitingForWarp:
                return TickWaitingForWarp();
            case Phase.Sleeping:
                return TickSleeping();
            case Phase.WaitingForDialog:
                return TickWaitingForDialog();
            default:
                return null;
        }
    }

    /// <inheritdoc />
    public V2Result Cancel()
    {
        _monitor.Log("[GoBed] Cancelled", LogLevel.Debug);
        _executor.CancelCurrent();
        var result = BuildResult(false, "cancelled");
        Reset();
        return result;
    }

    // ════════════════════════════════════════════════════════
    //  Tick phases (V1-style: simple & reliable)
    // ════════════════════════════════════════════════════════

    private V2Result? TickWaitingForWarp()
    {
        _waitTicks++;

        string currentMap = Game1.currentLocation?.NameOrUniqueName ?? "";
        if (IsHomeLocation(currentMap))
        {
            _monitor.Log($"[GoBed] Arrived at {currentMap} (waited {_waitTicks} ticks)", LogLevel.Debug);
            _waitTicks = 0;
            _phase = Phase.Sleeping;
            return null;
        }

        if (_waitTicks >= MaxWarpWaitFrames)
        {
            return Complete(false, "warp_timeout",
                $"Warp to {_homeLocationName} timed out ({MaxWarpWaitFrames} ticks), current={currentMap}");
        }

        return null;
    }

    /// <summary>
    /// Find bed, position player on it, trigger sleep via multiple methods.
    /// Uses BedFurniture.checkForAction first; if no dialog appears, retries with
    /// isInBed flag; ultimate fallback calls Game1.NewDay directly.
    /// </summary>
    private V2Result? TickSleeping()
    {
        var location = Game1.currentLocation;
        if (location == null || !IsHomeLocation(location.NameOrUniqueName))
        {
            return Complete(false, "not_at_home", $"Not in home location, current={location?.NameOrUniqueName}");
        }

        // Find bed — prefer double bed (same as V1)
        _bed = FindBed(location);

        if (_bed != null)
        {
            _bedTile = new Point((int)_bed.TileLocation.X, (int)_bed.TileLocation.Y);

            // Position player directly on the bed (V1 proven approach)
            // For multi-tile beds, position at TileLocation + (1,1) which is typically
            // the sleeping spot. This matches V1's bedCenterX/Y calculation.
            float posX = _bed.TileLocation.X * 64f + 64f;
            float posY = _bed.TileLocation.Y * 64f + 64f;
            Game1.player.Position = new Vector2(posX, posY);
            Game1.player.faceDirection(0); // Face up

            _monitor.Log($"[GoBed] Found bed '{_bed.Name}' type={_bed.bedType} at ({_bedTile.X},{_bedTile.Y}), " +
                $"player positioned at ({posX / 64f:F1},{posY / 64f:F1})", LogLevel.Info);

            // Method A: Set isInBed BEFORE checkForAction (SDV 1.6 may require this)
            Game1.player.isInBed.Value = true;
            _monitor.Log("[GoBed] Set isInBed=true, calling checkForAction...", LogLevel.Debug);

            bool checkResult = _bed.checkForAction(Game1.player);
            _monitor.Log($"[GoBed] checkForAction returned {checkResult}", LogLevel.Debug);

            _waitTicks = 0;
            _phase = Phase.WaitingForDialog;
        }
        else
        {
            // No BedFurniture found — log diagnostics and try fallback
            if (location is FarmHouse farmHouse)
            {
                var furnitureNames = farmHouse.furniture.Select(f => $"{f.Name}@({f.TileLocation.X},{f.TileLocation.Y})").ToList();
                _monitor.Log($"[GoBed] No BedFurniture found! Furniture: [{string.Join(", ", furnitureNames)}]", LogLevel.Warn);

                // Fallback: use getBedSpot + InteractWithTile
                var spot = farmHouse.getBedSpot();
                _monitor.Log($"[GoBed] Fallback to getBedSpot ({spot.X},{spot.Y})", LogLevel.Warn);
                Game1.player.setTileLocation(new Vector2(spot.X, spot.Y));
                Game1.player.isInBed.Value = true;
                _executor.InteractWithTile(spot.X, spot.Y);

                _bedTile = new Point(spot.X, spot.Y);
                _waitTicks = 0;
                _phase = Phase.WaitingForDialog;
            }
            else
            {
                return Complete(false, "no_bed", "No BedFurniture found and location is not FarmHouse");
            }
        }

        return null;
    }

    /// <summary>
    /// Wait for and confirm the sleep dialog.
    /// Multi-method approach with increasing aggression:
    /// - Ticks 1-10: Try answerDialogueAction + DialogueBox detection
    /// - Ticks 11-30: Retry checkForAction with isInBed, try dialog again
    /// - Ticks 31+: Direct Game1.NewDay as nuclear fallback
    /// </summary>
    private V2Result? TickWaitingForDialog()
    {
        _waitTicks++;

        // ── Method 1: Try answerDialogueAction("Sleep_Yes") ──────────
        // This works if FarmHouse has an active sleep question dialogue
        if (Game1.currentLocation != null)
        {
            try
            {
                bool answered = Game1.currentLocation.answerDialogueAction("Sleep_Yes", null);
                if (answered)
                {
                    _monitor.Log($"[GoBed] answerDialogueAction('Sleep_Yes') succeeded (tick {_waitTicks})", LogLevel.Info);
                    if (Game1.activeClickableMenu is DialogueBox)
                    {
                        Game1.activeClickableMenu = null;
                    }
                    return Complete(true, "sleep_confirmed", null);
                }
            }
            catch (Exception ex)
            {
                _monitor.Log($"[GoBed] answerDialogueAction exception: {ex.Message}", LogLevel.Trace);
            }
        }

        // ── Method 2: Detect and answer DialogueBox directly ─────────
        if (Game1.activeClickableMenu is DialogueBox dialogBox)
        {
            try
            {
                var responses = dialogBox.responses;
                if (responses != null && responses.Length > 0)
                {
                    _monitor.Log($"[GoBed] DialogueBox detected, responses={responses.Length}, selecting first (Yes)", LogLevel.Info);
                    Game1.activeClickableMenu = null;
                    Game1.currentLocation?.answerDialogueAction(responses[0].responseKey, null);
                    return Complete(true, "sleep_dialog_confirmed", null);
                }
                else
                {
                    // Info dialog (not a question) — click through it
                    _monitor.Log("[GoBed] Non-question DialogueBox, clicking through", LogLevel.Debug);
                    dialogBox.receiveLeftClick(0, 0);
                }
            }
            catch (Exception ex)
            {
                _monitor.Log($"[GoBed] DialogueBox handling exception: {ex.Message}", LogLevel.Trace);
            }
        }

        // ── Method 3: Retry bed interaction with isInBed pre-set ─────
        // At tick 10, re-attempt: set isInBed + re-trigger checkForAction
        // BedFurniture.checkForAction in SDV 1.6 may have different behavior
        // when isInBed is already true
        if (_waitTicks == 10 && _bed != null)
        {
            _monitor.Log("[GoBed] Retrying bed interaction at tick 10", LogLevel.Warn);
            Game1.player.isInBed.Value = true;
            _bed.checkForAction(Game1.player);
        }

        // ── Method 4: Direct sleep trigger via isInBed + answerDialogue ─
        if (_waitTicks == 20)
        {
            _monitor.Log("[GoBed] Tick 20: forcing isInBed + Sleep_Yes", LogLevel.Warn);
            try
            {
                Game1.player.isInBed.Value = true;
                bool answered = Game1.currentLocation?.answerDialogueAction("Sleep_Yes", null) ?? false;
                if (answered)
                {
                    if (Game1.activeClickableMenu is DialogueBox)
                    {
                        Game1.activeClickableMenu = null;
                    }
                    return Complete(true, "sleep_forced_dialog", null);
                }
                _monitor.Log($"[GoBed] answerDialogueAction returned false at tick 20", LogLevel.Warn);
            }
            catch (Exception ex)
            {
                _monitor.Log($"[GoBed] Forced sleep exception: {ex.Message}", LogLevel.Warn);
            }
        }

        // ── Method 5 (nuclear): Game1.NewDay — directly triggers end-of-day ──
        // Only if all dialog-based approaches have failed
        if (_waitTicks >= 30)
        {
            _monitor.Log("[GoBed] Tick 30: all dialog methods failed, calling Game1.NewDay(0f) directly", LogLevel.Warn);
            try
            {
                Game1.player.isInBed.Value = true;
                // Clean up any menu that might block the transition
                if (Game1.activeClickableMenu != null)
                {
                    _monitor.Log($"[GoBed] Closing active menu: {Game1.activeClickableMenu.GetType().Name}", LogLevel.Debug);
                    Game1.activeClickableMenu = null;
                }
                Game1.NewDay(0f);
                return Complete(true, "sleep_newday_direct", null);
            }
            catch (Exception ex)
            {
                _monitor.Log($"[GoBed] Game1.NewDay exception: {ex.Message}", LogLevel.Error);
                return Complete(false, "sleep_failed", $"Game1.NewDay failed: {ex.Message}");
            }
        }

        return null;
    }

    // ════════════════════════════════════════════════════════
    //  Helpers
    // ════════════════════════════════════════════════════════

    /// <summary>Check if the given map name matches the player's home location.</summary>
    private bool IsHomeLocation(string mapName)
    {
        if (string.IsNullOrEmpty(mapName)) return false;

        // Exact match (case-insensitive, supports GUID-suffixed names like "Cabin82b38ef3-...")
        if (string.Equals(mapName, _homeLocationName, StringComparison.OrdinalIgnoreCase)) return true;

        // Cabin variants: Cabin, Cabin1, Cabin2, Cabin<GUID>
        if (_homeLocationName.StartsWith("Cabin", StringComparison.OrdinalIgnoreCase)
            && mapName.StartsWith("Cabin", StringComparison.OrdinalIgnoreCase))
            return string.Equals(mapName, _homeLocationName, StringComparison.OrdinalIgnoreCase);

        // FarmHouse check
        if (string.Equals(_homeLocationName, "FarmHouse", StringComparison.OrdinalIgnoreCase)
            && string.Equals(mapName, "FarmHouse", StringComparison.OrdinalIgnoreCase)) return true;

        // Also check if it's a FarmHouse type location
        return Game1.currentLocation is FarmHouse;
    }

    /// <summary>Find a BedFurniture in the location. Prefers double bed.</summary>
    private static BedFurniture? FindBed(GameLocation? location)
    {
        if (location == null) return null;

        return location.furniture
            .OfType<BedFurniture>()
            .FirstOrDefault(b => b.bedType == BedFurniture.BedType.Double)
            ?? location.furniture
                .OfType<BedFurniture>()
                .FirstOrDefault();
    }

    /// <summary>Try to parse a bool from V2Command params.</summary>
    private static bool TryGetBool(Dictionary<string, object> args, string key)
    {
        if (!args.TryGetValue(key, out var obj) || obj == null)
            return false;

        return obj switch
        {
            bool b => b,
            string s => s.Equals("true", StringComparison.OrdinalIgnoreCase),
            JsonElement element when element.ValueKind == JsonValueKind.True => true,
            JsonElement element when element.ValueKind == JsonValueKind.False => false,
            _ => false
        };
    }

    // ════════════════════════════════════════════════════════
    //  Completion
    // ════════════════════════════════════════════════════════

    private V2Result Complete(bool success, string stoppedReason, string? errorMessage)
    {
        _monitor.Log($"[GoBed] Complete: success={success}, reason={stoppedReason}", success ? LogLevel.Info : LogLevel.Warn);

        V2Result result;
        if (success)
        {
            result = BuildResult(true, stoppedReason);
        }
        else
        {
            result = ResultBuilder.Failure(_seq, _actionId,
                stoppedReason.ToUpperInvariant(),
                errorMessage ?? stoppedReason);
        }

        Reset();
        return result;
    }

    private V2Result BuildResult(bool success, string stoppedReason)
    {
        var data = new Dictionary<string, object>
        {
            ["method"] = "warp",
            ["home_location"] = _homeLocationName,
            ["sleep_triggered"] = success && stoppedReason != "cancelled",
            ["stopped_reason"] = stoppedReason,
        };

        if (success)
        {
            var result = ResultBuilder.Success(_seq, _actionId, data)
                .WithObservation($"已回到 {_homeLocationName} 并触发睡觉");
            return result;
        }

        return ResultBuilder.Success(_seq, _actionId, data)
            .WithObservation($"回家睡觉未完成: {stoppedReason}");
    }

    private void Reset()
    {
        _phase = Phase.Inactive;
        _homeLocationName = "";
        _bedTile = default;
        _bed = null;
        _waitTicks = 0;
    }
}
