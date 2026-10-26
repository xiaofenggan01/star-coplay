using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.GameServices.Inventory;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// 统一菜单交互 L2 能力 — 声明式事务执行器。
///
/// L3 声明意图（买什么/存什么/做什么），引擎负责执行。
/// 内部按 menu_type 分派到不同策略：API 直调（Container/Craft/Ship）或 UI 模拟（Shop）。
///
/// v2.4.0：Shop/Container/Craft/Ship 四种场景。
/// v2.5.0+：Geode/Museum/Bundle/Cook（需 UI 自动化框架）。
/// </summary>
public class MenuInteractHandler : ICompoundHandler
{
    private static readonly HashSet<string> SupportedMenuTypes = new(StringComparer.OrdinalIgnoreCase)
    {
        "Shop", "Container", "Craft", "Ship"
    };

    private static readonly HashSet<string> PlannedMenuTypes = new(StringComparer.OrdinalIgnoreCase)
    {
        "Geode", "Museum", "Bundle", "Cook", "Forge", "Tailor"
    };

    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;

    // Shop 场景委托给 BuyItemsHandler（唯一需要异步多帧的场景）
    private readonly BuyItemsHandler _buyItemsHandler;
    private bool _delegatingToShop;

    // 命令上下文
    private int _seq;
    private string _actionId = "";
    private bool _isActive;

    public bool IsActive => _isActive || (_delegatingToShop && _buyItemsHandler.IsActive);

    public MenuInteractHandler(ActionExecutor executor, BuyItemsHandler buyItemsHandler, IMonitor monitor)
    {
        _executor = executor;
        _buyItemsHandler = buyItemsHandler;
        _monitor = monitor;
    }

    public V2Result? Start(V2Command cmd)
    {
        _seq = cmd.Seq;
        _actionId = cmd.ActionId;
        _delegatingToShop = false;
        _isActive = false;

        // 解析 menu_type
        if (!CompoundUtils.TryGetString(cmd.Params, "menu_type", out var menuType) ||
            string.IsNullOrWhiteSpace(menuType))
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.InvalidParams,
                "menu_interact 需要 menu_type 参数");
        }

        // 解析 operations
        if (!cmd.Params.TryGetValue("operations", out var opsObj) || opsObj == null)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.InvalidParams,
                "menu_interact 需要 operations 参数");
        }

        var operations = ParseOperations(opsObj);
        if (operations == null || operations.Count == 0)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.InvalidParams,
                "operations 为空或格式错误");
        }

        // 未实现的 MenuType
        if (PlannedMenuTypes.Contains(menuType))
        {
            return ResultBuilder.Failure(_seq, _actionId,
                "NOT_IMPLEMENTED",
                $"menu_type '{menuType}' 尚未实现（计划 v2.5.0+）。" +
                $"当前支持: {string.Join(", ", SupportedMenuTypes)}");
        }

        if (!SupportedMenuTypes.Contains(menuType))
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.InvalidParams,
                $"未知的 menu_type: {menuType}。" +
                $"当前支持: {string.Join(", ", SupportedMenuTypes)}。" +
                $"计划中: {string.Join(", ", PlannedMenuTypes)}");
        }

        _monitor.Log($"[MenuInteract] menu_type={menuType}, operations={operations.Count}", LogLevel.Info);

        // 分派
        return menuType.ToLowerInvariant() switch
        {
            "shop" => HandleShop(cmd, operations),
            "container" => HandleContainer(cmd, operations),
            "craft" => HandleCraft(cmd, operations),
            "ship" => HandleShip(cmd, operations),
            _ => ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.ExecutionFailed, $"未处理的 menu_type: {menuType}")
        };
    }

    public V2Result? Tick()
    {
        if (_delegatingToShop)
        {
            var result = _buyItemsHandler.Tick();
            if (result != null)
            {
                _delegatingToShop = false;
                _isActive = false;
                // 包装为 menu_interact 格式
                return WrapShopResult(result);
            }
            return null;
        }
        return null;
    }

    public V2Result Cancel()
    {
        if (_delegatingToShop)
        {
            _delegatingToShop = false;
            _isActive = false;
            return _buyItemsHandler.Cancel();
        }
        _isActive = false;
        return ResultBuilder.Failure(_seq, _actionId,
            "CANCELLED", "menu_interact 已取消");
    }

    // ════════════════════════════════════════════════════════
    //  Shop（异步，委托 BuyItemsHandler）
    // ════════════════════════════════════════════════════════

    private V2Result? HandleShop(V2Command originalCmd, List<Dictionary<string, object>> operations)
    {
        // 从 operations 构造 buy_items 的 items 参数
        var items = new List<Dictionary<string, object>>();
        foreach (var op in operations)
        {
            var action = GetString(op, "action") ?? "";
            if (!string.Equals(action, "buy", StringComparison.OrdinalIgnoreCase))
            {
                _monitor.Log($"[MenuInteract] Shop 仅支持 buy 操作，跳过 {action}", LogLevel.Warn);
                continue;
            }
            var item = GetString(op, "item");
            var qty = GetInt(op, "qty", 1);
            if (item != null)
                items.Add(new Dictionary<string, object> { ["name"] = item, ["quantity"] = qty });
        }

        if (items.Count == 0)
        {
            return ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.InvalidParams,
                "Shop operations 中没有有效的 buy 操作");
        }

        // 获取 shop 标识（从 target 或直接 shop 参数）
        CompoundUtils.TryGetString(originalCmd.Params, "target", out var target);
        CompoundUtils.TryGetString(originalCmd.Params, "shop", out var shop);
        var shopId = shop ?? target ?? "";

        // 构造 buy_items V2Command
        var buyCmd = new V2Command
        {
            Seq = _seq,
            ActionId = _actionId,
            ProtocolVersion = "2.0",
            Capability = "buy_items",
            Params = new Dictionary<string, object>
            {
                ["shop"] = shopId,
                ["items"] = JsonSerializer.Serialize(items),
            }
        };

        _delegatingToShop = true;
        _isActive = true;
        var startResult = _buyItemsHandler.Start(buyCmd);

        if (startResult != null)
        {
            // 同步完成（验证失败等）
            _delegatingToShop = false;
            _isActive = false;
            return WrapShopResult(startResult);
        }

        return null; // 异步执行中，等 Tick
    }

    private V2Result WrapShopResult(V2Result buyResult)
    {
        // buy_items 的结果已经很完整，直接透传，加上 menu_type 标记
        if (buyResult.Data is Dictionary<string, object> data)
        {
            data["menu_type"] = "Shop";
        }
        return buyResult;
    }

    // ════════════════════════════════════════════════════════
    //  Container（同步，API 直调）
    // ════════════════════════════════════════════════════════

    private V2Result HandleContainer(V2Command cmd, List<Dictionary<string, object>> operations)
    {
        CompoundUtils.TryGetString(cmd.Params, "target", out var containerId);

        var results = new List<object>();
        int completed = 0, failed = 0;

        foreach (var op in operations)
        {
            var action = GetString(op, "action") ?? "";
            var item = GetString(op, "item") ?? "";
            var qty = GetInt(op, "qty", -1); // -1 = all

            if (string.IsNullOrEmpty(item))
            {
                results.Add(new { action, item, success = false, error = "缺少 item 参数" });
                failed++;
                continue;
            }

            try
            {
                var cm = new ContainerManager(_monitor);
                if (string.Equals(action, "put", StringComparison.OrdinalIgnoreCase) && containerId != null)
                {
                    var opResult = cm.StoreItem(containerId, item, qty);
                    results.Add(new { action, item, qty = opResult.Transferred, remaining_in_inventory = opResult.RemainingCount, success = opResult.Success });
                    if (opResult.Success) completed++; else failed++;
                }
                else if (string.Equals(action, "take", StringComparison.OrdinalIgnoreCase) && containerId != null)
                {
                    var opResult = cm.TakeItem(containerId, item, qty);
                    results.Add(new { action, item, qty = opResult.Transferred, remaining_in_container = opResult.RemainingCount, success = opResult.Success });
                    if (opResult.Success) completed++; else failed++;
                }
                else
                {
                    results.Add(new { action, item, success = false, error = $"Container 不支持 {action} 操作，或缺少 target（container_id）" });
                    failed++;
                }
            }
            catch (Exception ex)
            {
                results.Add(new { action, item, success = false, error = ex.Message });
                failed++;
            }
        }

        return BuildMenuResult("Container", operations.Count, completed, failed, results);
    }

    // ════════════════════════════════════════════════════════
    //  Craft（同步，API 直调）
    // ════════════════════════════════════════════════════════

    private V2Result HandleCraft(V2Command cmd, List<Dictionary<string, object>> operations)
    {
        var player = Game1.player;
        var results = new List<object>();
        int completed = 0, failed = 0;

        foreach (var op in operations)
        {
            var item = GetString(op, "item") ?? "";
            var qty = GetInt(op, "qty", 1);

            if (string.IsNullOrEmpty(item))
            {
                results.Add(new { action = "craft", item, success = false, error = "缺少 item 参数" });
                failed++;
                continue;
            }

            // 模糊匹配配方
            var matchedRecipe = player.craftingRecipes.Keys
                .FirstOrDefault(r => r.Equals(item, StringComparison.OrdinalIgnoreCase))
                ?? player.craftingRecipes.Keys
                    .FirstOrDefault(r => r.Contains(item, StringComparison.OrdinalIgnoreCase));

            if (matchedRecipe == null)
            {
                results.Add(new { action = "craft", item, success = false, error = $"未找到配方: {item}" });
                failed++;
                continue;
            }

            var recipe = new CraftingRecipe(matchedRecipe);
            int crafted = 0;

            for (int i = 0; i < qty; i++)
            {
                if (!recipe.doesFarmerHaveIngredientsInInventory())
                    break;
                recipe.consumeIngredients(null);
                var created = recipe.createItem();
                if (!player.addItemToInventoryBool(created))
                    Game1.createItemDebris(created, player.getStandingPosition(), player.FacingDirection);
                crafted++;
            }

            results.Add(new { action = "craft", item = matchedRecipe, qty_crafted = crafted, qty_requested = qty, success = crafted > 0 });
            if (crafted > 0) completed++; else failed++;
        }

        return BuildMenuResult("Craft", operations.Count, completed, failed, results);
    }

    // ════════════════════════════════════════════════════════
    //  Ship（同步，API 直调）
    // ════════════════════════════════════════════════════════

    private V2Result HandleShip(V2Command cmd, List<Dictionary<string, object>> operations)
    {
        var player = Game1.player;
        var farm = Game1.getFarm();
        var results = new List<object>();
        int completed = 0, failed = 0;

        foreach (var op in operations)
        {
            var item = GetString(op, "item") ?? "";
            var qty = GetInt(op, "qty", -1);

            if (string.IsNullOrEmpty(item))
            {
                results.Add(new { action = "ship", item, success = false, error = "缺少 item 参数" });
                failed++;
                continue;
            }

            int shipped = 0;
            for (int i = 0; i < player.Items.Count && (qty == -1 || shipped < qty); i++)
            {
                var invItem = player.Items[i];
                if (invItem == null) continue;
                if (!invItem.Name.Equals(item, StringComparison.OrdinalIgnoreCase) &&
                    !invItem.DisplayName.Equals(item, StringComparison.OrdinalIgnoreCase))
                    continue;

                int toShip = qty == -1 ? invItem.Stack : Math.Min(qty - shipped, invItem.Stack);
                var clone = invItem.getOne();
                clone.Stack = toShip;
                farm.getShippingBin(player).Add(clone);

                invItem.Stack -= toShip;
                if (invItem.Stack <= 0)
                    player.Items[i] = null;
                shipped += toShip;
            }

            results.Add(new { action = "ship", item, qty_shipped = shipped, success = shipped > 0 });
            if (shipped > 0) completed++; else failed++;
        }

        return BuildMenuResult("Ship", operations.Count, completed, failed, results);
    }

    // ════════════════════════════════════════════════════════
    //  工具方法
    // ════════════════════════════════════════════════════════

    private V2Result BuildMenuResult(string menuType, int requested, int completed, int failed, List<object> results)
    {
        var data = new Dictionary<string, object>
        {
            ["menu_type"] = menuType,
            ["operations_requested"] = requested,
            ["operations_completed"] = completed,
            ["operations_failed"] = failed,
            ["results"] = results,
        };

        var result = completed > 0
            ? ResultBuilder.Success(_seq, _actionId, data)
            : ResultBuilder.Failure(_seq, _actionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"所有 {menuType} 操作均失败");
        if (completed > 0 && failed > 0)
            result = ResultBuilder.Partial(_seq, _actionId, data);
        result.WithObservation($"{menuType}: {completed}/{requested} 操作成功");
        return result;
    }

    private static List<Dictionary<string, object>>? ParseOperations(object opsObj)
    {
        if (opsObj is JsonElement je && je.ValueKind == JsonValueKind.Array)
        {
            var list = new List<Dictionary<string, object>>();
            foreach (var elem in je.EnumerateArray())
            {
                if (elem.ValueKind != JsonValueKind.Object) continue;
                var dict = new Dictionary<string, object>();
                foreach (var prop in elem.EnumerateObject())
                {
                    dict[prop.Name] = prop.Value.ValueKind switch
                    {
                        JsonValueKind.String => prop.Value.GetString()!,
                        JsonValueKind.Number => prop.Value.GetInt32(),
                        _ => prop.Value.ToString()
                    };
                }
                list.Add(dict);
            }
            return list;
        }

        if (opsObj is List<object> objList)
        {
            var list = new List<Dictionary<string, object>>();
            foreach (var item in objList)
            {
                if (item is Dictionary<string, object> dict)
                    list.Add(dict);
            }
            return list;
        }

        return null;
    }

    private static string? GetString(Dictionary<string, object> dict, string key)
    {
        if (dict.TryGetValue(key, out var val))
            return val?.ToString();
        return null;
    }

    private static int GetInt(Dictionary<string, object> dict, string key, int defaultValue = 0)
    {
        if (!dict.TryGetValue(key, out var val)) return defaultValue;
        if (val is int i) return i;
        if (val is long l) return (int)l;
        if (val is JsonElement je && je.TryGetInt32(out var ji)) return ji;
        if (int.TryParse(val?.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed))
            return parsed;
        return defaultValue;
    }
}
