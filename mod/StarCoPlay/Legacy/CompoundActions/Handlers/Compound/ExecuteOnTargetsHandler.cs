using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// Generic L2 compound handler that executes an action (use_tool or do) on a list of TargetRef targets.
/// Replaces specialized batch handlers with a unified loop:
///   resolve target -> navigate to adjacent tile -> face target -> equip tool/execute action ->
///   wait animation -> check stop conditions -> next target.
///
/// Supports repeat_per_target modes:
///   - "once": single action per target (default)
///   - "until_gone": repeat until target disappears (trees, rocks)
///   - "count:N": repeat exactly N times per target
///
/// State machine: Scanning -> Navigating -> Facing -> Acting -> WaitingAnimation -> (loop) -> Scanning
/// </summary>
public class ExecuteOnTargetsHandler : ICompoundHandler
{
    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;

    // ── State machine ──────────────────────────────────────────────
    private enum Phase
    {
        Inactive,
        Scanning,
        Navigating,
        Facing,
        Acting,
        WaitingAnimation,
    }

    private Phase _phase = Phase.Inactive;

    // ── Command context ────────────────────────────────────────────
    private int _seq;
    private string _actionId = "";
    private string _action = "";       // "use_tool" or "do"
    private string? _tool;             // tool name for use_tool
    private string? _toolCategory;     // tool category (e.g. "pickaxe") — takes priority over _tool
    private string? _intent;           // intent for do
    private Dictionary<string, object>? _intentArgs;
    private string _repeatMode = "once";
    private int _repeatCount = 1;      // for "count:N" mode
    private const int MaxRepeatsPerTarget = 10; // 硬上限，防止 until_gone/count 无限循环
    private int _maxCount = int.MaxValue;
    private int _staminaBelow = 0;     // 0 = no stamina check
    private int _timeAfter = 0;        // 0 = no time check

    // ── Working state ──────────────────────────────────────────────
    private Queue<string> _targets = new();
    private string _currentTargetRef = "";
    private Point _currentTargetTile;
    private string _currentTargetLocation = "";
    private int _completedCount;
    private int _skippedCount;
    private int _totalCount;
    private float _staminaStart;
    private int _animWaitTicks;
    private int _currentRepeatsDone;   // repeats done on current target
    private string _startLocation = "";

    // ── Constants ──────────────────────────────────────────────────
    private const int AnimationTimeout = 120;
    private const int FacingWaitTicks = 1;

    /// <inheritdoc />
    public bool IsActive => _phase != Phase.Inactive;

    public ExecuteOnTargetsHandler(ActionExecutor executor, IMonitor monitor)
    {
        _executor = executor;
        _monitor = monitor;
    }

    /// <inheritdoc />
    public V2Result? Start(V2Command cmd)
    {
        _seq = cmd.Seq;
        _actionId = cmd.ActionId;

        // ── Parse params ──────────────────────────────────────────

        // Required: targets array
        if (!TryGetTargetsList(cmd.Params, out var targetsList) || targetsList.Count == 0)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.InvalidParams,
                "execute_on_targets requires non-empty 'targets' array");
        }

        // Required: action
        if (!CompoundUtils.TryGetString(cmd.Params, "action", out var action) ||
            (action != "use_tool" && action != "do"))
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.InvalidParams,
                "execute_on_targets requires 'action' parameter: 'use_tool' or 'do'");
        }
        _action = action;

        // Optional: tool_category (e.g. "pickaxe", "axe") — preferred over tool name
        CompoundUtils.TryGetString(cmd.Params, "tool_category", out var toolCategory);
        _toolCategory = string.IsNullOrEmpty(toolCategory) ? null : toolCategory;

        // Optional: tool (for use_tool) — fallback when tool_category is not set
        CompoundUtils.TryGetString(cmd.Params, "tool", out var tool);
        _tool = string.IsNullOrEmpty(tool) ? null : tool;

        // Optional: intent (for do)
        CompoundUtils.TryGetString(cmd.Params, "intent", out var intent);
        _intent = string.IsNullOrEmpty(intent) ? null : intent;

        // Optional: intent_args
        _intentArgs = TryGetDictionary(cmd.Params, "intent_args");

        // Validate: use_tool requires tool or tool_category, do requires intent
        if (_action == "use_tool" && _tool == null && _toolCategory == null)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.InvalidParams,
                "action='use_tool' requires 'tool' or 'tool_category' parameter");
        }

        // Optional: repeat_per_target
        _repeatMode = "once";
        _repeatCount = 1;
        if (CompoundUtils.TryGetString(cmd.Params, "repeat_per_target", out var repeat))
        {
            if (repeat == "once" || repeat == "until_gone")
            {
                _repeatMode = repeat;
            }
            else if (repeat.StartsWith("count:", StringComparison.OrdinalIgnoreCase))
            {
                _repeatMode = "count";
                if (int.TryParse(repeat.AsSpan(6), NumberStyles.Integer, CultureInfo.InvariantCulture, out var n) && n > 0)
                    _repeatCount = Math.Min(n, MaxRepeatsPerTarget);
                else
                    _repeatCount = 1;
            }
        }

        // until_gone 对移动实体（animal/npc）降级为 once，因为 IsTargetStillPresent 只检查静态地图元素
        if (_repeatMode == "until_gone" && targetsList.Any(t =>
            t.Contains(":animal_", StringComparison.OrdinalIgnoreCase) ||
            t.Contains(":npc_", StringComparison.OrdinalIgnoreCase)))
        {
            _monitor.Log(
                "[ExecuteOnTargets] until_gone 不适用于移动实体（animal/npc），降级为 once",
                LogLevel.Warn);
            _repeatMode = "once";
        }

        // Optional: max_count
        if (CompoundUtils.TryGetInt(cmd.Params, "max_count", out int mc) && mc > 0)
            _maxCount = mc;
        else
            _maxCount = int.MaxValue;

        // Optional: stop_conditions
        _staminaBelow = 0;
        _timeAfter = 0;
        var stopConditions = TryGetDictionary(cmd.Params, "stop_conditions");
        if (stopConditions != null)
        {
            if (CompoundUtils.TryGetInt(stopConditions, "stamina_below", out int sb))
                _staminaBelow = sb;
            if (CompoundUtils.TryGetInt(stopConditions, "time_after", out int ta))
                _timeAfter = ta;
        }

        // ── Validate preconditions ────────────────────────────────

        // Check tool exists in inventory if action=use_tool
        if (_action == "use_tool")
        {
            if (_toolCategory != null)
            {
                // Resolve tool_category to actual tool, then equip by name
                var foundTool = CompoundUtils.FindToolByCategory(Game1.player, _toolCategory);
                if (foundTool == null)
                {
                    return ResultBuilder.Failure(_seq, _actionId,
                        ResultBuilder.Errors.ItemNotFound,
                        $"Tool category '{_toolCategory}' not found in inventory");
                }
                // Use the resolved tool's Name for subsequent equip calls
                _tool = foundTool.Name;
                _monitor.Log($"[ExecuteOnTargets] Resolved tool_category '{_toolCategory}' to '{_tool}' ({foundTool.DisplayName})", LogLevel.Debug);
            }

            if (_tool != null)
            {
                var equipResult = _executor.EquipItemByName(_tool);
                if (!equipResult.Success)
                {
                    return ResultBuilder.Failure(_seq, _actionId,
                        ResultBuilder.Errors.ItemNotFound,
                        $"Tool not found in inventory: {_tool}");
                }
            }
        }

        var location = Game1.currentLocation;
        if (location == null)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.ExecutionFailed, "No active location");
        }
        _startLocation = location.NameOrUniqueName;
        _staminaStart = Game1.player.Stamina;

        // ── Initialize state ──────────────────────────────────────

        _targets = new Queue<string>(targetsList);
        _totalCount = targetsList.Count;
        _completedCount = 0;
        _skippedCount = 0;
        _animWaitTicks = 0;
        _currentRepeatsDone = 0;

        _monitor.Log($"[ExecuteOnTargets] Starting: {_totalCount} targets, action={_action}, " +
            $"tool={_tool ?? "none"}, intent={_intent ?? "none"}, repeat={_repeatMode}", LogLevel.Info);

        _phase = Phase.Scanning;
        return null;
    }

    /// <inheritdoc />
    public V2Result? Tick()
    {
        return _phase switch
        {
            Phase.Scanning => TickScanning(),
            Phase.Navigating => TickNavigating(),
            Phase.Facing => TickFacing(),
            Phase.Acting => TickActing(),
            Phase.WaitingAnimation => TickWaitingAnimation(),
            _ => null,
        };
    }

    /// <inheritdoc />
    public V2Result Cancel()
    {
        _monitor.Log($"[ExecuteOnTargets] Cancelled: completed={_completedCount}, skipped={_skippedCount}", LogLevel.Debug);
        _executor.CancelCurrent();
        var result = BuildResult("cancelled");
        Reset();
        return result;
    }

    // ── Phase: Scanning ────────────────────────────────────────────

    private V2Result? TickScanning()
    {
        // Check stop conditions first
        if (_completedCount >= _maxCount)
            return Complete("max_count");

        if (_staminaBelow > 0 && Game1.player.Stamina < _staminaBelow)
            return Complete("stamina_low");

        if (_timeAfter > 0 && Game1.timeOfDay >= _timeAfter)
            return Complete("time_exceeded");

        if (!string.Equals(Game1.currentLocation?.NameOrUniqueName, _startLocation, StringComparison.OrdinalIgnoreCase))
            return Complete("map_changed");

        if (_targets.Count == 0)
            return Complete("all_done");

        _currentTargetRef = _targets.Dequeue();
        _currentRepeatsDone = 0;

        // Resolve TargetRef to tile position
        if (!ResolveTargetRef(_currentTargetRef, out var targetTile, out var targetLocation))
        {
            _monitor.Log($"[ExecuteOnTargets] Cannot resolve target: {_currentTargetRef}, skipping", LogLevel.Debug);
            _skippedCount++;
            return null; // Stay in Scanning
        }

        _currentTargetTile = targetTile;
        _currentTargetLocation = targetLocation;

        // Check target is in current location
        if (!string.Equals(_currentTargetLocation, Game1.currentLocation?.NameOrUniqueName, StringComparison.OrdinalIgnoreCase))
        {
            _monitor.Log($"[ExecuteOnTargets] Target {_currentTargetRef} is in {_currentTargetLocation}, " +
                $"but player is in {Game1.currentLocation?.NameOrUniqueName}, skipping", LogLevel.Debug);
            _skippedCount++;
            return null;
        }

        // Find adjacent walkable tile
        var location = Game1.currentLocation!;
        var adjacent = CompoundUtils.FindAdjacentWalkable(location, _currentTargetTile);
        if (adjacent == null)
        {
            _monitor.Log($"[ExecuteOnTargets] No walkable tile adjacent to {_currentTargetRef}, skipping", LogLevel.Debug);
            _skippedCount++;
            return null;
        }

        // Navigate to adjacent tile
        if (!_executor.NavigateTo(adjacent.Value))
        {
            _monitor.Log($"[ExecuteOnTargets] Navigation failed for {_currentTargetRef}, skipping", LogLevel.Debug);
            _skippedCount++;
            return null;
        }

        _phase = Phase.Navigating;
        _monitor.Log($"[ExecuteOnTargets] Navigating to target {_currentTargetRef} " +
            $"at ({_currentTargetTile.X},{_currentTargetTile.Y})", LogLevel.Debug);
        return null;
    }

    // ── Phase: Navigating ──────────────────────────────────────────

    private V2Result? TickNavigating()
    {
        if (_executor.IsBusy)
            return null;

        var playerTile = Game1.player.TilePoint;
        int dist = Math.Abs(playerTile.X - _currentTargetTile.X)
                 + Math.Abs(playerTile.Y - _currentTargetTile.Y);

        if (dist > 2)
        {
            _monitor.Log($"[ExecuteOnTargets] Nav ended too far from {_currentTargetRef} (dist={dist}), skipping", LogLevel.Debug);
            _skippedCount++;
            _phase = Phase.Scanning;
            return null;
        }

        // Face the target
        int dir = CompoundUtils.GetFacingDirection(playerTile, _currentTargetTile);
        Game1.player.faceDirection(dir);

        _phase = Phase.Facing;
        _animWaitTicks = 0;
        return null;
    }

    // ── Phase: Facing ──────────────────────────────────────────────

    private V2Result? TickFacing()
    {
        if (_animWaitTicks < FacingWaitTicks)
        {
            _animWaitTicks++;
            return null;
        }

        // Equip tool if needed
        if (_action == "use_tool" && _tool != null)
        {
            var currentTool = Game1.player.CurrentTool;
            if (currentTool == null ||
                (!currentTool.DisplayName.Equals(_tool, StringComparison.OrdinalIgnoreCase) &&
                 !currentTool.Name.Equals(_tool, StringComparison.OrdinalIgnoreCase)))
            {
                var equipResult = _executor.EquipItemByName(_tool);
                if (!equipResult.Success)
                {
                    _monitor.Log($"[ExecuteOnTargets] Cannot equip {_tool}, stopping", LogLevel.Warn);
                    return Complete("tool_missing");
                }
            }
        }

        _phase = Phase.Acting;
        return null;
    }

    // ── Phase: Acting ──────────────────────────────────────────────

    private V2Result? TickActing()
    {
        bool actionOk;

        if (_action == "use_tool")
        {
            // Re-equip if somehow changed
            if (_tool != null)
            {
                var currentTool = Game1.player.CurrentTool;
                if (currentTool == null ||
                    (!currentTool.DisplayName.Equals(_tool, StringComparison.OrdinalIgnoreCase) &&
                     !currentTool.Name.Equals(_tool, StringComparison.OrdinalIgnoreCase)))
                {
                    _executor.EquipItemByName(_tool);
                }
            }

            actionOk = _executor.UseToolAt(_currentTargetTile.X, _currentTargetTile.Y);
        }
        else // action == "do"
        {
            if (_intent != null)
            {
                // Build a V2Command with intent for InteractHandler via ActionExecutor
                actionOk = ExecuteDoWithIntent(_currentTargetTile, _intent, _intentArgs);
            }
            else
            {
                // Generic do — press action button at target tile
                actionOk = _executor.InteractWithTile(_currentTargetTile.X, _currentTargetTile.Y);
            }
        }

        if (!actionOk)
        {
            _monitor.Log($"[ExecuteOnTargets] Action failed on {_currentTargetRef}, skipping", LogLevel.Debug);
            _skippedCount++;
            _phase = Phase.Scanning;
            return null;
        }

        _currentRepeatsDone++;
        _phase = Phase.WaitingAnimation;
        _animWaitTicks = 0;
        return null;
    }

    // ── Phase: WaitingAnimation ────────────────────────────────────

    private V2Result? TickWaitingAnimation()
    {
        _animWaitTicks++;

        // For "do" actions (harvest, pet, etc.) that use InteractWithTile,
        // the animation completes almost immediately (no UsingTool state).
        // Only use_tool actions go through the tool animation pipeline.
        if (_action == "use_tool")
        {
            if (!Game1.player.UsingTool && Game1.player.canMove)
            {
                return OnActionComplete();
            }

            if (_animWaitTicks >= AnimationTimeout)
            {
                _monitor.Log("[ExecuteOnTargets] Animation timeout, force-resetting", LogLevel.Warn);
                CompoundUtils.ForceResetToolState(Game1.player);
                return OnActionComplete();
            }

            return null;
        }
        else
        {
            // For "do" actions (harvest, pet, etc.):
            // checkAction() may trigger item-obtained popups or dialogue boxes
            // that set canMove=false. We must wait for them to clear.
            //
            // Bug fix: previously used fixed 5-tick wait, which was too short
            // when harvest triggers "obtained item" popup → canMove stays false
            // → NavigateTo to next target fails → all remaining targets skipped.
            //
            // Now: condition-based wait (same principle as use_tool path),
            // with min 3 ticks to allow next-frame dialogue creation.
            if (_animWaitTicks >= 3
                && Game1.player.canMove
                && Game1.activeClickableMenu == null)
            {
                return OnActionComplete();
            }

            // Timeout protection: force-dismiss and continue
            if (_animWaitTicks >= AnimationTimeout)
            {
                _monitor.Log("[ExecuteOnTargets] do-action animation timeout, force-continuing", LogLevel.Warn);
                // Force-dismiss any lingering dialogue/popup
                if (Game1.activeClickableMenu != null)
                {
                    _monitor.Log($"[ExecuteOnTargets] Dismissing lingering menu: {Game1.activeClickableMenu.GetType().Name}", LogLevel.Debug);
                    Game1.activeClickableMenu = null;
                }
                return OnActionComplete();
            }

            return null;
        }
    }

    private V2Result? OnActionComplete()
    {
        // Check if we need more repeats on this target
        // 硬上限：无论模式，单目标最多重复 MaxRepeatsPerTarget 次
        if (_currentRepeatsDone >= MaxRepeatsPerTarget)
        {
            _monitor.Log(
                $"[ExecuteOnTargets] {_currentTargetRef} 达到重复上限 {MaxRepeatsPerTarget}，继续下一个",
                LogLevel.Warn);
        }

        bool needMoreRepeats = _currentRepeatsDone >= MaxRepeatsPerTarget ? false : _repeatMode switch
        {
            "once" => false,
            "until_gone" => IsTargetStillPresent(_currentTargetTile),
            "count" => _currentRepeatsDone < _repeatCount,
            _ => false,
        };

        if (needMoreRepeats)
        {
            // Check stop conditions before repeating
            if (_staminaBelow > 0 && Game1.player.Stamina < _staminaBelow)
                return Complete("stamina_low");

            if (_timeAfter > 0 && Game1.timeOfDay >= _timeAfter)
                return Complete("time_exceeded");

            // Continue repeating on same target
            _phase = Phase.Acting;
            return null;
        }

        // Target done
        _completedCount++;
        _monitor.Log($"[ExecuteOnTargets] Completed {_currentTargetRef} after {_currentRepeatsDone} actions, " +
            $"total completed={_completedCount}", LogLevel.Debug);

        _phase = Phase.Scanning;
        return null;
    }

    // ── Target resolution ──────────────────────────────────────────

    /// <summary>
    /// 将 TargetRef 字符串解析为 tile 坐标和 locationName。
    /// 委托给 TargetRefResolver（支持 11 类 entityKind，含向后兼容别名）。
    /// </summary>
    private bool ResolveTargetRef(string targetRef, out Point tile, out string locationName)
    {
        tile = default;
        locationName = "";

        var resolved = TargetRefResolver.Resolve(targetRef, _monitor);
        if (resolved == null)
            return false;

        tile = new Point((int)resolved.CurrentPosition.X, (int)resolved.CurrentPosition.Y);
        locationName = resolved.Location.NameOrUniqueName;
        return true;
    }

    /// <summary>
    /// Check if a target (terrain feature, object, etc.) still exists at the given tile.
    /// Used for "until_gone" repeat mode.
    /// </summary>
    private static bool IsTargetStillPresent(Point tile)
    {
        var location = Game1.currentLocation;
        if (location == null) return false;

        var key = new Vector2(tile.X, tile.Y);

        // Check terrain features (trees, crops, etc.)
        if (location.terrainFeatures.ContainsKey(key))
            return true;

        // Check objects (stones, placed items, etc.)
        if (location.objects.ContainsKey(key))
            return true;

        // Check resource clumps (large stumps, boulders)
        foreach (var clump in location.resourceClumps)
        {
            if ((int)clump.Tile.X == tile.X && (int)clump.Tile.Y == tile.Y)
                return true;
        }

        return false;
    }

    /// <summary>
    /// Execute a "do" action with a specific intent.
    /// For simple intents (harvest, pet, pick_up), delegates to InteractWithTile.
    /// For intents requiring extra args, uses the appropriate executor method.
    /// </summary>
    private bool ExecuteDoWithIntent(Point tile, string intent, Dictionary<string, object>? intentArgs)
    {
        var normalizedIntent = intent.Trim().ToLowerInvariant();

        switch (normalizedIntent)
        {
            case "harvest":
            case "pick_up":
            case "examine":
                // These intents map to a simple checkAction interaction
                return _executor.InteractWithTile(tile.X, tile.Y);

            case "pet":
                // Pet uses InteractWithTile on the animal's tile
                Game1.player.faceGeneralDirection(new Vector2(tile.X * 64, tile.Y * 64));
                return _executor.InteractWithTile(tile.X, tile.Y);

            case "talk":
                // Talk uses InteractWithTile — NPC must be within range
                return _executor.InteractWithTile(tile.X, tile.Y);

            case "place":
                // Place requires an item argument
                if (intentArgs != null && CompoundUtils.TryGetString(intentArgs, "item", out var itemName))
                {
                    // Equip the item, then interact
                    _executor.EquipItemByName(itemName);
                    return _executor.InteractWithTile(tile.X, tile.Y);
                }
                return _executor.InteractWithTile(tile.X, tile.Y);

            default:
                // Generic fallback: just interact
                return _executor.InteractWithTile(tile.X, tile.Y);
        }
    }

    // ── Param parsing helpers ──────────────────────────────────────

    /// <summary>Extract targets list from params. Supports JSON array of strings.</summary>
    private static bool TryGetTargetsList(Dictionary<string, object> args, out List<string> targets)
    {
        targets = new List<string>();

        if (!args.TryGetValue("targets", out var raw) || raw == null)
            return false;

        switch (raw)
        {
            case List<object> list:
                foreach (var item in list)
                {
                    var str = item?.ToString();
                    if (!string.IsNullOrWhiteSpace(str))
                        targets.Add(str);
                }
                return targets.Count > 0;

            case JsonElement element when element.ValueKind == JsonValueKind.Array:
                foreach (var item in element.EnumerateArray())
                {
                    var str = item.ValueKind == JsonValueKind.String
                        ? item.GetString()
                        : item.ToString();
                    if (!string.IsNullOrWhiteSpace(str))
                        targets.Add(str);
                }
                return targets.Count > 0;

            case string[] arr:
                foreach (var s in arr)
                {
                    if (!string.IsNullOrWhiteSpace(s))
                        targets.Add(s);
                }
                return targets.Count > 0;

            default:
                return false;
        }
    }

    /// <summary>Extract a nested dictionary from params.</summary>
    private static Dictionary<string, object>? TryGetDictionary(Dictionary<string, object> args, string key)
    {
        if (!args.TryGetValue(key, out var raw) || raw == null)
            return null;

        if (raw is Dictionary<string, object> dict)
            return dict;

        if (raw is JsonElement element && element.ValueKind == JsonValueKind.Object)
        {
            var result = new Dictionary<string, object>();
            foreach (var prop in element.EnumerateObject())
            {
                object? val = prop.Value.ValueKind switch
                {
                    JsonValueKind.String => prop.Value.GetString(),
                    JsonValueKind.Number when prop.Value.TryGetInt64(out var l) => l,
                    JsonValueKind.Number => prop.Value.GetDouble(),
                    JsonValueKind.True => true,
                    JsonValueKind.False => false,
                    _ => null
                };
                if (val != null)
                    result[prop.Name] = val;
            }
            return result;
        }

        return null;
    }

    // ── Completion helpers ──────────────────────────────────────────

    private V2Result Complete(string stoppedReason)
    {
        _monitor.Log($"[ExecuteOnTargets] Complete: completed={_completedCount}, skipped={_skippedCount}, " +
            $"total={_totalCount}, reason={stoppedReason}", LogLevel.Info);
        var result = BuildResult(stoppedReason);
        Reset();
        return result;
    }

    private V2Result BuildResult(string stoppedReason)
    {
        float staminaUsed = _staminaStart - Game1.player.Stamina;

        var data = new Dictionary<string, object>
        {
            ["completed"] = _completedCount,
            ["skipped"] = _skippedCount,
            ["total"] = _totalCount,
            ["stopped_reason"] = stoppedReason,
            ["location"] = Game1.currentLocation?.NameOrUniqueName ?? "Unknown",
        };

        if (staminaUsed > 0)
            data["stamina_used"] = (int)Math.Ceiling(staminaUsed);

        var result = ResultBuilder.Success(_seq, _actionId, data);

        if (staminaUsed > 0)
            result.WithSideEffect("stamina", $"-{(int)Math.Ceiling(staminaUsed)}");

        return result;
    }

    private void Reset()
    {
        _phase = Phase.Inactive;
        _targets.Clear();
        _currentTargetRef = "";
        _currentTargetTile = default;
        _currentTargetLocation = "";
        _completedCount = 0;
        _skippedCount = 0;
        _totalCount = 0;
        _staminaStart = 0;
        _animWaitTicks = 0;
        _currentRepeatsDone = 0;
        _startLocation = "";
        _toolCategory = null;
    }
}
