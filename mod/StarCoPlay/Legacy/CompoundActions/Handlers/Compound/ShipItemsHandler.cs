using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// Compound handler for batch-shipping items via the shipping bin.
/// Supports two modes:
///   - plan (default): scan inventory and return a preview without side effects.
///   - execute: actually ship items to the shipping bin for next-day settlement.
///
/// Both modes are synchronous — Start() returns V2Result directly, Tick() is never called.
/// </summary>
public class ShipItemsHandler : ICompoundHandler
{
    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;

    // ── Quality constants (Stardew Valley internal values) ────────────────
    private const int QualityNormal = 0;
    private const int QualitySilver = 1;
    private const int QualityGold = 2;
    private const int QualityIridium = 4;

    // ── Default excluded categories (when items param is empty) ──────────
    private static readonly HashSet<string> DefaultExcludeKeywords = new(StringComparer.OrdinalIgnoreCase)
    {
        "Seeds", "Wood", "Stone", "Fiber", "Coal", "Sap"
    };

    /// <inheritdoc />
    public bool IsActive => false; // Always synchronous, never active between frames.

    public ShipItemsHandler(ActionExecutor executor, IMonitor monitor)
    {
        _executor = executor;
        _monitor = monitor;
    }

    /// <summary>
    /// Execute the ship_items action synchronously.
    /// Returns V2Result directly for both plan and execute modes.
    /// </summary>
    public V2Result? Start(V2Command cmd)
    {
        var args = cmd.Params ?? new Dictionary<string, object>();

        // Parse mode
        string mode = "plan";
        if (args.TryGetValue("mode", out var modeVal))
        {
            var modeStr = ParseString(modeVal);
            if (modeStr == "execute") mode = "execute";
        }

        // Parse items / exclude / min_quality
        var requestedItems = ParseStringArray(args, "items");
        var excludeItems = ParseStringArray(args, "exclude");
        int minQualityThreshold = ParseMinQuality(args);

        var player = Game1.player;
        var farm = Game1.getFarm();
        if (farm == null)
        {
            return ResultBuilder.Failure(cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                "Cannot access farm — shipping bin unavailable");
        }

        // Scan inventory for shippable items
        var candidates = new List<ShipCandidate>();
        var kept = new List<Dictionary<string, object>>();
        var excluded = new List<string>();

        for (int i = 0; i < player.Items.Count; i++)
        {
            var item = player.Items[i];
            if (item == null) continue;

            // Only StardewValley.Object items can be shipped
            if (item is not StardewValley.Object obj)
            {
                // Tools and non-object items are silently skipped
                if (item is StardewValley.Tool)
                    excluded.Add(item.DisplayName);
                continue;
            }

            if (!obj.canBeShipped())
            {
                excluded.Add(obj.DisplayName);
                continue;
            }

            // Check explicit exclude list
            if (excludeItems.Any(ex =>
                obj.DisplayName.Equals(ex, StringComparison.OrdinalIgnoreCase) ||
                obj.Name.Equals(ex, StringComparison.OrdinalIgnoreCase)))
            {
                excluded.Add(obj.DisplayName);
                continue;
            }

            // If items list specified, only include matching items
            if (requestedItems.Count > 0)
            {
                bool matched = requestedItems.Any(req =>
                    obj.DisplayName.Equals(req, StringComparison.OrdinalIgnoreCase) ||
                    obj.Name.Equals(req, StringComparison.OrdinalIgnoreCase));
                if (!matched)
                    continue; // Not in requested list, silently skip
            }
            else
            {
                // Default filter: exclude seeds and basic materials
                if (IsDefaultExcluded(obj))
                {
                    excluded.Add(obj.DisplayName);
                    continue;
                }
            }

            // Quality filter: keep items at or above min_quality threshold
            if (minQualityThreshold > 0 && obj.Quality >= minQualityThreshold)
            {
                kept.Add(new Dictionary<string, object>
                {
                    ["name"] = obj.DisplayName,
                    ["count"] = obj.Stack,
                    ["quality"] = QualityName(obj.Quality),
                    ["reason"] = "quality_filter"
                });
                continue;
            }

            candidates.Add(new ShipCandidate
            {
                SlotIndex = i,
                Name = obj.DisplayName,
                Count = obj.Stack,
                UnitPrice = obj.sellToStorePrice(),
                Quality = obj.Quality,
                Item = obj
            });
        }

        if (mode == "plan")
            return BuildPlanResult(cmd, candidates, kept, excluded);
        else
            return ExecuteShipping(cmd, candidates, kept, excluded, player, farm);
    }

    /// <inheritdoc />
    public V2Result? Tick()
    {
        // Synchronous handler — Tick is never called.
        return null;
    }

    /// <inheritdoc />
    public V2Result Cancel()
    {
        // Synchronous handler — Cancel returns empty result.
        return ResultBuilder.Success(0, "", new Dictionary<string, object>
        {
            ["mode"] = "cancelled",
            ["shipped"] = new List<object>(),
            ["total_value"] = 0,
            ["message"] = "ship_items was cancelled (synchronous, nothing to cancel)"
        });
    }

    // ── Plan mode ────────────────────────────────────────────────────────

    private V2Result BuildPlanResult(V2Command cmd,
        List<ShipCandidate> candidates,
        List<Dictionary<string, object>> kept,
        List<string> excluded)
    {
        var itemsList = candidates.Select(c => new Dictionary<string, object>
        {
            ["name"] = c.Name,
            ["count"] = c.Count,
            ["unit_price"] = c.UnitPrice,
            ["total"] = c.UnitPrice * c.Count,
            ["quality"] = QualityName(c.Quality)
        }).ToList();

        int totalValue = candidates.Sum(c => c.UnitPrice * c.Count);
        int totalCount = candidates.Sum(c => c.Count);

        var data = new Dictionary<string, object>
        {
            ["mode"] = "plan",
            ["items"] = itemsList,
            ["total_value"] = totalValue,
            ["item_count"] = totalCount,
            ["kept"] = kept,
            ["excluded"] = excluded.Distinct().ToList(),
            ["message"] = $"计划出货 {totalCount} 件物品，预计收入 {totalValue}g。保留 {kept.Count} 件高品质物品。"
        };

        return ResultBuilder.Success(cmd.Seq, cmd.ActionId, data);
    }

    // ── Execute mode ─────────────────────────────────────────────────────

    private V2Result ExecuteShipping(V2Command cmd,
        List<ShipCandidate> candidates,
        List<Dictionary<string, object>> kept,
        List<string> excluded,
        Farmer player, Farm farm)
    {
        var shipped = new List<Dictionary<string, object>>();
        var failed = new List<Dictionary<string, object>>();
        int totalValue = 0;

        // Process from end to start to avoid index shifting when removing items
        foreach (var candidate in candidates.OrderByDescending(c => c.SlotIndex))
        {
            try
            {
                var item = player.Items[candidate.SlotIndex];
                if (item == null || item != candidate.Item)
                {
                    // Item moved since scan — skip
                    failed.Add(new Dictionary<string, object>
                    {
                        ["name"] = candidate.Name,
                        ["reason"] = "item_moved"
                    });
                    continue;
                }

                // Remove from inventory and add to shipping bin
                player.Items[candidate.SlotIndex] = null;
                farm.getShippingBin(player).Add(item);

                int value = candidate.UnitPrice * candidate.Count;
                totalValue += value;

                shipped.Add(new Dictionary<string, object>
                {
                    ["name"] = candidate.Name,
                    ["count"] = candidate.Count,
                    ["value"] = value
                });

                _monitor.Log($"[ShipItems] Shipped: {candidate.Name} x{candidate.Count} ({value}g)", LogLevel.Debug);
            }
            catch (Exception ex)
            {
                _monitor.Log($"[ShipItems] Failed to ship {candidate.Name}: {ex.Message}", LogLevel.Warn);
                failed.Add(new Dictionary<string, object>
                {
                    ["name"] = candidate.Name,
                    ["reason"] = ex.Message
                });
            }
        }

        int shippedCount = shipped.Sum(s => (int)(s.TryGetValue("count", out var c) ? c : 0));

        var data = new Dictionary<string, object>
        {
            ["mode"] = "execute",
            ["shipped"] = shipped,
            ["total_value"] = totalValue,
            ["failed"] = failed,
            ["message"] = $"已出货 {shippedCount} 件物品，明日结算 {totalValue}g"
        };

        var result = ResultBuilder.Success(cmd.Seq, cmd.ActionId, data);
        if (totalValue > 0)
            result.WithSideEffect("gold", $"+{totalValue}g (next day)");

        return result;
    }

    // ── Helpers ───────────────────────────────────────────────────────────

    private static bool IsDefaultExcluded(StardewValley.Object obj)
    {
        // Exclude tools (already handled above)
        // Exclude seeds: Name contains "Seeds"
        if (obj.Name.Contains("Seeds", StringComparison.OrdinalIgnoreCase))
            return true;

        // Exclude basic materials
        foreach (var keyword in DefaultExcludeKeywords)
        {
            if (obj.Name.Equals(keyword, StringComparison.OrdinalIgnoreCase))
                return true;
        }

        return false;
    }

    private static int ParseMinQuality(Dictionary<string, object> args)
    {
        if (!args.TryGetValue("min_quality", out var val))
            return 0; // No filter

        var str = ParseString(val).ToLowerInvariant();
        return str switch
        {
            "silver" => QualitySilver,
            "gold" => QualityGold,
            "iridium" => QualityIridium,
            _ => 0
        };
    }

    private static string QualityName(int quality) => quality switch
    {
        QualitySilver => "Silver",
        QualityGold => "Gold",
        QualityIridium => "Iridium",
        _ => "Normal"
    };

    private static List<string> ParseStringArray(Dictionary<string, object> args, string key)
    {
        if (!args.TryGetValue(key, out var val)) return new List<string>();

        if (val is JsonElement elem)
        {
            if (elem.ValueKind == JsonValueKind.Array)
            {
                var list = new List<string>();
                foreach (var item in elem.EnumerateArray())
                    if (item.ValueKind == JsonValueKind.String)
                        list.Add(item.GetString() ?? "");
                return list;
            }
            if (elem.ValueKind == JsonValueKind.String)
                return new List<string> { elem.GetString() ?? "" };
        }

        if (val is string s) return new List<string> { s };
        return new List<string>();
    }

    private static string ParseString(object val)
    {
        if (val is string s) return s;
        if (val is JsonElement elem && elem.ValueKind == JsonValueKind.String)
            return elem.GetString() ?? "";
        return val?.ToString() ?? "";
    }

    // ── Internal types ───────────────────────────────────────────────────

    private class ShipCandidate
    {
        public int SlotIndex;
        public string Name = "";
        public int Count;
        public int UnitPrice;
        public int Quality;
        public StardewValley.Object? Item;
    }
}
