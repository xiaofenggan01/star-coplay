using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.GameData.Shops;
using StardewValley.Internal;
using StardewValley.Menus;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// Compound handler: buy_items — navigate to a shop NPC, open the shop menu, purchase items.
/// State machine: ResolveShop → NavigateToNpc → WaitArrival → InteractNpc → WaitForMenu → PurchaseItems → CloseMenu → Complete
///
/// Uses ShopMenu's public purchase API. Does NOT manually manipulate gold/inventory.
/// </summary>
public class BuyItemsHandler : ICompoundHandler
{
    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;

    // ── State machine ──────────────────────────────────────────────
    private enum Phase
    {
        Inactive,
        ResolveShop,
        NavigateToNpc,
        WaitArrival,
        InteractNpc,
        WaitForMenu,
        PurchaseItems,
        CloseMenu,
    }

    private Phase _phase = Phase.Inactive;

    // ── Command context ────────────────────────────────────────────
    private int _seq;
    private string _actionId = "";

    // ── Parameters ─────────────────────────────────────────────────
    private string _shopId = "";
    private List<BuySpec> _requestedItems = new();

    // ── Working state ──────────────────────────────────────────────
    private string _npcName = "";
    private string _targetLocation = "";
    private int _targetX, _targetY;
    private int _waitTicks;
    private int _navTicks;
    private int _retryCount;
    private int _boughtCount;
    private int _totalCost;
    private List<Dictionary<string, object>> _boughtItems = new();
    private List<Dictionary<string, object>> _failedItems = new();

    // ── Constants ──────────────────────────────────────────────────
    private const int MaxNavFrames = 600;      // ~10 seconds
    private const int MaxWaitMenuFrames = 180;  // ~3 seconds
    private const int MaxCloseFrames = 60;      // ~1 second
    private const int MaxNavRetries = 2;

    // ── Shop NPC mapping (NPC name + indoor location + counter tile) ──
    private static readonly Dictionary<string, (string npc, string location, int x, int y)> ShopNpcMap = new()
    {
        ["SeedShop"]   = ("Pierre",  "SeedShop",    4, 19),
        ["FishShop"]   = ("Willy",   "FishShop",    5,  6),
        ["Saloon"]     = ("Gus",     "Saloon",     10, 18),
        ["Blacksmith"] = ("Clint",   "Blacksmith",  3, 13),
        ["AnimalShop"] = ("Marnie",  "AnimalShop", 12, 16),
        ["Carpenter"]  = ("Robin",   "ScienceHouse", 8, 18),
        ["Hospital"]   = ("Harvey",  "Hospital",    8, 19),
    };

    /// <inheritdoc />
    public bool IsActive => _phase != Phase.Inactive;

    public BuyItemsHandler(ActionExecutor executor, IMonitor monitor)
    {
        _executor = executor;
        _monitor = monitor;
    }

    /// <inheritdoc />
    public V2Result? Start(V2Command cmd)
    {
        _seq = cmd.Seq;
        _actionId = cmd.ActionId;
        Reset();

        // Parse shop parameter
        if (!CompoundUtils.TryGetString(cmd.Params, "shop", out var shopId) || string.IsNullOrWhiteSpace(shopId))
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.InvalidParams, "buy_items 缺少 shop 参数");
        }
        _shopId = shopId;

        // Parse items parameter
        _requestedItems = ParseItems(cmd.Params);
        if (_requestedItems.Count == 0)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.InvalidParams, "buy_items 缺少有效的 items 列表");
        }

        _monitor.Log($"[BuyItems] Start: shop={_shopId}, items={_requestedItems.Count}", LogLevel.Info);
        _phase = Phase.ResolveShop;
        return null; // Async — caller should Tick() each frame
    }

    /// <inheritdoc />
    public V2Result? Tick()
    {
        return _phase switch
        {
            Phase.ResolveShop     => TickResolveShop(),
            Phase.NavigateToNpc   => TickNavigateToNpc(),
            Phase.WaitArrival     => TickWaitArrival(),
            Phase.InteractNpc     => TickInteractNpc(),
            Phase.WaitForMenu     => TickWaitForMenu(),
            Phase.PurchaseItems   => TickPurchaseItems(),
            Phase.CloseMenu       => TickCloseMenu(),
            _ => null,
        };
    }

    /// <inheritdoc />
    public V2Result Cancel()
    {
        _monitor.Log("[BuyItems] Cancelled", LogLevel.Debug);
        _executor.CancelCurrent();
        var result = Complete(false, "cancelled");
        return result;
    }

    // ════════════════════════════════════════════════════════
    //  Phase: ResolveShop
    // ════════════════════════════════════════════════════════

    private V2Result? TickResolveShop()
    {
        if (ShopNpcMap.TryGetValue(_shopId, out var mapping))
        {
            _npcName = mapping.npc;
            _targetLocation = mapping.location;
            _targetX = mapping.x;
            _targetY = mapping.y;
        }
        else if (_shopId == "JojaMart")
        {
            _npcName = "";
            _targetLocation = "JojaMart";
            _targetX = 11;
            _targetY = 25;
        }
        else
        {
            return Complete(false, $"unknown_shop:{_shopId}");
        }

        _monitor.Log($"[BuyItems] Resolved: npc={_npcName}, loc={_targetLocation}, tile=({_targetX},{_targetY})", LogLevel.Debug);

        // If already in the target location, skip warp
        string currentMap = Game1.currentLocation?.NameOrUniqueName ?? "";
        if (currentMap.Equals(_targetLocation, StringComparison.OrdinalIgnoreCase))
        {
            return StartNavigateToCounter();
        }

        // Warp to the shop's indoor location
        bool ok = _executor.WarpTo(_targetLocation, _targetX, _targetY);
        if (!ok)
        {
            return Complete(false, $"warp_failed:{_targetLocation}");
        }

        _phase = Phase.WaitArrival;
        _waitTicks = 0;
        return null;
    }

    // ════════════════════════════════════════════════════════
    //  Navigate to NPC/counter within map
    // ════════════════════════════════════════════════════════

    private V2Result? StartNavigateToCounter()
    {
        var location = Game1.currentLocation;
        if (location == null)
        {
            return Complete(false, "no_location");
        }

        // If we have an NPC, try to navigate near them
        if (!string.IsNullOrEmpty(_npcName))
        {
            var npc = Game1.getCharacterFromName(_npcName);
            if (npc != null && string.Equals(npc.currentLocation?.NameOrUniqueName, location.NameOrUniqueName, StringComparison.OrdinalIgnoreCase))
            {
                var adjacent = CompoundUtils.FindAdjacentWalkable(location, npc.TilePoint);
                if (adjacent != null && _executor.NavigateTo(adjacent.Value))
                {
                    _phase = Phase.NavigateToNpc;
                    _navTicks = 0;
                    _monitor.Log($"[BuyItems] Navigating to NPC {_npcName} at ({adjacent.Value.X},{adjacent.Value.Y})", LogLevel.Debug);
                    return null;
                }
            }
        }

        // Fallback: navigate to the mapped counter tile
        var standTile = CompoundUtils.FindAdjacentWalkable(location, new Point(_targetX, _targetY));
        if (standTile != null && _executor.NavigateTo(standTile.Value))
        {
            _phase = Phase.NavigateToNpc;
            _navTicks = 0;
            return null;
        }

        // Can't navigate — try interacting directly (might be close enough)
        _phase = Phase.InteractNpc;
        _waitTicks = 0;
        return null;
    }

    private V2Result? TickNavigateToNpc()
    {
        _navTicks++;

        if (!_executor.IsBusy)
        {
            _phase = Phase.InteractNpc;
            _waitTicks = 0;
            return null;
        }

        if (_navTicks >= MaxNavFrames)
        {
            _monitor.Log("[BuyItems] Navigation timed out", LogLevel.Warn);
            _executor.CancelCurrent();
            _phase = Phase.InteractNpc;
            _waitTicks = 0;
            return null;
        }

        return null;
    }

    // ════════════════════════════════════════════════════════
    //  Phase: WaitArrival — wait after warp
    // ════════════════════════════════════════════════════════

    private V2Result? TickWaitArrival()
    {
        _waitTicks++;

        string currentMap = Game1.currentLocation?.NameOrUniqueName ?? "";
        if (currentMap.Equals(_targetLocation, StringComparison.OrdinalIgnoreCase) && Context.CanPlayerMove)
        {
            _monitor.Log($"[BuyItems] Arrived at {currentMap}", LogLevel.Debug);
            return StartNavigateToCounter();
        }

        if (_waitTicks >= 300)
        {
            return Complete(false, $"warp_timeout:{_targetLocation}");
        }

        return null;
    }

    // ════════════════════════════════════════════════════════
    //  Phase: InteractNpc — face and interact
    // ════════════════════════════════════════════════════════

    private V2Result? TickInteractNpc()
    {
        var player = Game1.player;
        Point interactTile;

        if (!string.IsNullOrEmpty(_npcName))
        {
            var npc = Game1.getCharacterFromName(_npcName);
            if (npc != null && string.Equals(npc.currentLocation?.NameOrUniqueName, Game1.currentLocation?.NameOrUniqueName, StringComparison.OrdinalIgnoreCase))
            {
                interactTile = npc.TilePoint;
            }
            else
            {
                interactTile = new Point(_targetX, _targetY);
            }
        }
        else
        {
            interactTile = new Point(_targetX, _targetY);
        }

        // Face the NPC/counter
        int dir = CompoundUtils.GetFacingDirection(player.TilePoint, interactTile);
        player.faceDirection(dir);

        // Interact
        _executor.InteractWithTile(interactTile.X, interactTile.Y);

        _phase = Phase.WaitForMenu;
        _waitTicks = 0;
        return null;
    }

    // ════════════════════════════════════════════════════════
    //  Phase: WaitForMenu — wait for ShopMenu
    // ════════════════════════════════════════════════════════

    private V2Result? TickWaitForMenu()
    {
        _waitTicks++;

        // ShopMenu opened
        if (Game1.activeClickableMenu is ShopMenu)
        {
            _monitor.Log("[BuyItems] ShopMenu detected", LogLevel.Debug);
            _phase = Phase.PurchaseItems;
            return null;
        }

        // DialogueBox — auto-advance (NPC greeting before shop)
        if (Game1.activeClickableMenu is DialogueBox dialogue)
        {
            if (dialogue.isQuestion && dialogue.responses != null && dialogue.responses.Length > 0)
            {
                // Look for a "Shop" or "Buy" response
                for (int i = 0; i < dialogue.responses.Length; i++)
                {
                    var key = dialogue.responses[i].responseKey ?? "";
                    if (key.Contains("Shop", StringComparison.OrdinalIgnoreCase) ||
                        key.Contains("Buy", StringComparison.OrdinalIgnoreCase))
                    {
                        _executor.AnswerDialog(i);
                        _monitor.Log($"[BuyItems] Selected dialog option: {key}", LogLevel.Debug);
                        _waitTicks = 0;
                        return null;
                    }
                }
                // No shop option — try first option
                _executor.AnswerDialog(0);
                _waitTicks = 0;
            }
            else
            {
                dialogue.receiveLeftClick(0, 0);
            }
            return null;
        }

        // Retry interaction if no menu appeared
        if (_waitTicks >= 60 && _retryCount < MaxNavRetries)
        {
            _retryCount++;
            _monitor.Log($"[BuyItems] No menu after {_waitTicks} ticks, retrying (attempt {_retryCount})", LogLevel.Debug);
            _phase = Phase.InteractNpc;
            _waitTicks = 0;
            return null;
        }

        if (_waitTicks >= MaxWaitMenuFrames)
        {
            return Complete(false, "shop_closed");
        }

        return null;
    }

    // ════════════════════════════════════════════════════════
    //  Phase: PurchaseItems — buy from open ShopMenu
    // ════════════════════════════════════════════════════════

    private V2Result? TickPurchaseItems()
    {
        var shopMenu = Game1.activeClickableMenu as ShopMenu;
        if (shopMenu == null)
        {
            return Complete(false, "shop_menu_closed_unexpectedly");
        }

        var player = Game1.player;

        foreach (var spec in _requestedItems)
        {
            // Find item in forSale list
            ISalable? salable = null;
            foreach (var item in shopMenu.forSale)
            {
                if (item.DisplayName.Equals(spec.Name, StringComparison.OrdinalIgnoreCase) ||
                    item.Name.Equals(spec.Name, StringComparison.OrdinalIgnoreCase))
                {
                    salable = item;
                    break;
                }
            }

            if (salable == null)
            {
                _failedItems.Add(new Dictionary<string, object>
                {
                    ["name"] = spec.Name,
                    ["reason"] = "not_in_stock"
                });
                continue;
            }

            // Get stock info — SDV 1.6: itemPriceAndStock is Dictionary<ISalable, ItemStockInformation>
            if (!shopMenu.itemPriceAndStock.TryGetValue(salable, out var stockInfo))
            {
                _failedItems.Add(new Dictionary<string, object>
                {
                    ["name"] = spec.Name,
                    ["reason"] = "price_not_found"
                });
                continue;
            }

            int price = stockInfo.Price > 0 ? stockInfo.Price : salable.salePrice();
            int boughtForThisItem = 0;

            for (int i = 0; i < spec.Quantity; i++)
            {
                if (player.Money < price)
                {
                    if (boughtForThisItem == 0)
                    {
                        _failedItems.Add(new Dictionary<string, object>
                        {
                            ["name"] = spec.Name,
                            ["reason"] = "insufficient_gold",
                            ["need"] = price,
                            ["have"] = player.Money,
                        });
                    }
                    break;
                }

                // Purchase through ShopMenu's receiveLeftClick — same as player clicking.
                // This ensures ALL game-internal logic runs: gold deduction, inventory,
                // achievements, statistics, NPC reactions, stock updates.
                // NEVER manually modify player.Money or player.Items.

                // Find the item's index in forSale list
                int saleIdx = shopMenu.forSale.IndexOf(salable);
                if (saleIdx < 0) break;

                // Scroll to make the item visible in forSaleButtons
                // forSaleButtons shows items starting at currentItemIndex
                int visibleSlots = shopMenu.forSaleButtons.Count;
                if (saleIdx < shopMenu.currentItemIndex || saleIdx >= shopMenu.currentItemIndex + visibleSlots)
                {
                    shopMenu.currentItemIndex = Math.Max(0, saleIdx);
                }

                // Calculate which forSaleButton corresponds to this item
                int buttonIdx = saleIdx - shopMenu.currentItemIndex;
                if (buttonIdx < 0 || buttonIdx >= shopMenu.forSaleButtons.Count)
                {
                    if (boughtForThisItem == 0)
                    {
                        _failedItems.Add(new Dictionary<string, object>
                        {
                            ["name"] = spec.Name,
                            ["reason"] = "ui_scroll_failed",
                        });
                    }
                    break;
                }

                // Click the button center — ShopMenu.receiveLeftClick handles everything
                int moneyBefore = player.Money;
                var btn = shopMenu.forSaleButtons[buttonIdx];
                shopMenu.receiveLeftClick(btn.bounds.Center.X, btn.bounds.Center.Y, playSound: true);

                // Verify purchase succeeded (money decreased)
                int actualCost = moneyBefore - player.Money;
                if (actualCost <= 0)
                {
                    // Click didn't result in a purchase
                    if (boughtForThisItem == 0)
                    {
                        _failedItems.Add(new Dictionary<string, object>
                        {
                            ["name"] = spec.Name,
                            ["reason"] = "purchase_failed",
                        });
                    }
                    break;
                }

                boughtForThisItem++;
                _boughtCount++;
                _totalCost += actualCost;
            }

            if (boughtForThisItem > 0)
            {
                _boughtItems.Add(new Dictionary<string, object>
                {
                    ["name"] = salable.DisplayName,
                    ["count"] = boughtForThisItem,
                    ["unit_price"] = price,
                    ["total"] = price * boughtForThisItem
                });
                _monitor.Log($"[BuyItems] Bought: {salable.DisplayName} x{boughtForThisItem} ({price * boughtForThisItem}g)", LogLevel.Debug);
            }
        }

        _phase = Phase.CloseMenu;
        _waitTicks = 0;
        return null;
    }

    // ════════════════════════════════════════════════════════
    //  Phase: CloseMenu
    // ════════════════════════════════════════════════════════

    private V2Result? TickCloseMenu()
    {
        _waitTicks++;

        if (Game1.activeClickableMenu is ShopMenu shopMenu)
        {
            shopMenu.exitThisMenu(false);
        }

        if (Game1.activeClickableMenu == null || _waitTicks >= MaxCloseFrames)
        {
            return Complete(true, _failedItems.Count > 0 ? "partial" : "all_done");
        }

        return null;
    }

    // ════════════════════════════════════════════════════════
    //  Completion & Result
    // ════════════════════════════════════════════════════════

    private V2Result Complete(bool success, string reason)
    {
        _monitor.Log($"[BuyItems] Complete: success={success}, reason={reason}, bought={_boughtCount}, cost={_totalCost}",
            success ? LogLevel.Info : LogLevel.Warn);

        var data = new Dictionary<string, object>
        {
            ["shop"] = _shopId,
            ["bought"] = _boughtItems,
            ["bought_count"] = _boughtCount,
            ["total_cost"] = _totalCost,
            ["gold_remaining"] = Game1.player?.Money ?? 0,
            ["failed"] = _failedItems,
            ["stopped_reason"] = reason,
        };

        V2Result result;
        if (_boughtCount > 0)
        {
            result = (_failedItems.Count > 0
                    ? ResultBuilder.Partial(_seq, _actionId, data)
                    : ResultBuilder.Success(_seq, _actionId, data))
                .WithObservation($"从 {_shopId} 购买了 {_boughtCount} 件物品，花费 {_totalCost}g");
            if (_totalCost > 0)
                result.WithSideEffect("gold", $"-{_totalCost}g");
        }
        else if (success && _failedItems.Count == 0)
        {
            result = ResultBuilder.Success(_seq, _actionId, data)
                .WithObservation($"从 {_shopId} 购买了 0 件物品，花费 0g");
        }
        else
        {
            result = ResultBuilder.Failure(_seq, _actionId,
                GetFailureCode(reason), $"购买失败: {reason}");
        }

        Reset();
        return result;
    }

    private string GetFailureCode(string reason)
    {
        if (reason == "shop_closed" || reason == "shop_menu_closed_unexpectedly")
            return ResultBuilder.Errors.ShopClosed;
        if (reason.StartsWith("unknown_shop:", StringComparison.Ordinal))
            return ResultBuilder.Errors.ShopNotFound;
        if (_failedItems.Any(item => Equals(item.GetValueOrDefault("reason"), "insufficient_gold")))
            return ResultBuilder.Errors.InsufficientFunds;
        if (_failedItems.Any(item => Equals(item.GetValueOrDefault("reason"), "not_in_stock") ||
                                     Equals(item.GetValueOrDefault("reason"), "price_not_found")))
            return ResultBuilder.Errors.ItemNotInShop;
        return reason.ToUpperInvariant();
    }

    private void Reset()
    {
        _phase = Phase.Inactive;
        _shopId = "";
        _requestedItems = new();
        _npcName = "";
        _targetLocation = "";
        _targetX = 0;
        _targetY = 0;
        _waitTicks = 0;
        _navTicks = 0;
        _retryCount = 0;
        _boughtCount = 0;
        _totalCost = 0;
        _boughtItems = new();
        _failedItems = new();
    }

    // ════════════════════════════════════════════════════════
    //  Item parsing
    // ════════════════════════════════════════════════════════

    private static List<BuySpec> ParseItems(Dictionary<string, object> args)
    {
        var result = new List<BuySpec>();

        if (!args.TryGetValue("items", out var itemsValue) || itemsValue == null)
            return result;

        // Handle string (JSON-encoded array)
        string? jsonStr = null;
        if (itemsValue is string s)
        {
            jsonStr = s;
        }
        else if (itemsValue is JsonElement elem)
        {
            if (elem.ValueKind == JsonValueKind.String)
            {
                jsonStr = elem.GetString();
            }
            else if (elem.ValueKind == JsonValueKind.Array)
            {
                return ParseJsonArray(elem);
            }
        }
        else if (itemsValue is IEnumerable items)
        {
            return ParseEnumerable(items);
        }

        if (!string.IsNullOrWhiteSpace(jsonStr))
        {
            try
            {
                var doc = JsonDocument.Parse(jsonStr);
                if (doc.RootElement.ValueKind == JsonValueKind.Array)
                {
                    return ParseJsonArray(doc.RootElement);
                }
            }
            catch
            {
                // Not valid JSON — try comma-separated names
                foreach (var name in jsonStr.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
                {
                    result.Add(new BuySpec { Name = name, Quantity = 1 });
                }
            }
        }

        return result;
    }

    private static List<BuySpec> ParseEnumerable(IEnumerable items)
    {
        var result = new List<BuySpec>();
        foreach (var item in items)
        {
            if (item is string name)
            {
                if (!string.IsNullOrWhiteSpace(name))
                    result.Add(new BuySpec { Name = name, Quantity = 1 });
                continue;
            }

            if (item is JsonElement element)
            {
                if (element.ValueKind == JsonValueKind.Object && TryParseJsonObject(element, out var jsonSpec))
                    result.Add(jsonSpec);
                continue;
            }

            if (item is IDictionary<string, object> values && TryParseDictionary(values, out var spec))
                result.Add(spec);
        }
        return result;
    }

    private static List<BuySpec> ParseJsonArray(JsonElement array)
    {
        var result = new List<BuySpec>();
        foreach (var item in array.EnumerateArray())
        {
            if (item.ValueKind == JsonValueKind.Object)
            {
                if (TryParseJsonObject(item, out var spec))
                    result.Add(spec);
            }
            else if (item.ValueKind == JsonValueKind.String)
            {
                var name = item.GetString();
                if (!string.IsNullOrWhiteSpace(name))
                    result.Add(new BuySpec { Name = name!, Quantity = 1 });
            }
        }
        return result;
    }

    private static bool TryParseJsonObject(JsonElement item, out BuySpec spec)
    {
        spec = new BuySpec();
        var name = item.TryGetProperty("name", out var nameValue) ? nameValue.GetString() : null;
        if (string.IsNullOrWhiteSpace(name))
            return false;

        var quantity = 1;
        if (item.TryGetProperty("count", out var countValue))
        {
            if (!TryReadPositiveInt(countValue, out quantity))
                return false;
        }
        else if (item.TryGetProperty("quantity", out var quantityValue) &&
                 !TryReadPositiveInt(quantityValue, out quantity))
        {
            return false;
        }

        spec = new BuySpec { Name = name!, Quantity = quantity };
        return true;
    }

    private static bool TryParseDictionary(IDictionary<string, object> item, out BuySpec spec)
    {
        spec = new BuySpec();
        if (!item.TryGetValue("name", out var nameValue) || nameValue is not string name ||
            string.IsNullOrWhiteSpace(name))
        {
            return false;
        }

        var quantity = 1;
        if (item.TryGetValue("count", out var countValue))
        {
            if (!TryReadPositiveInt(countValue, out quantity))
                return false;
        }
        else if (item.TryGetValue("quantity", out var quantityValue) &&
                 !TryReadPositiveInt(quantityValue, out quantity))
        {
            return false;
        }

        spec = new BuySpec { Name = name, Quantity = quantity };
        return true;
    }

    private static bool TryReadPositiveInt(object? value, out int quantity)
    {
        quantity = value switch
        {
            int number => number,
            long number when number <= int.MaxValue && number >= int.MinValue => (int)number,
            double number when number <= int.MaxValue && number >= int.MinValue && number == Math.Truncate(number) => (int)number,
            string text when int.TryParse(text, out var parsed) => parsed,
            JsonElement element when element.ValueKind == JsonValueKind.Number && element.TryGetInt32(out var parsed) => parsed,
            JsonElement element when element.ValueKind == JsonValueKind.String && int.TryParse(element.GetString(), out var parsed) => parsed,
            _ => 0,
        };
        return quantity > 0;
    }

    private class BuySpec
    {
        public string Name = "";
        public int Quantity = 1;
    }
}
