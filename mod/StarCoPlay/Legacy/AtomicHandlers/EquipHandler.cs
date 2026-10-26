// ============================================================
// [LEGACY] 旧版装备切换实现 — 已被当前 equip 执行原子替代
// 保留原因：UseInputBridgeHandlers=false 时的 fallback
// 计划：protocol-capability-completion 迭代中评估清理
// ============================================================
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.Json;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2;

/// <summary>
/// 处理 equip(item) 能力：切换手持物品。
/// 支持按物品名称或快捷栏槽位装备。
/// </summary>
public class EquipHandler
{
    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;

    public EquipHandler(ActionExecutor executor, IMonitor monitor)
    {
        _executor = executor;
        _monitor = monitor;
    }

    public V2Result? Handle(V2Command cmd)
    {
        // 优先按槽位装备
        if (TryGetInt(cmd.Params, "slot", out var slot))
        {
            var ok = _executor.EquipItem(slot);
            if (!ok)
            {
                return ResultBuilder.Failure(
                    cmd.Seq, cmd.ActionId,
                    ResultBuilder.Errors.ItemNotFound,
                    $"槽位 {slot} 没有可装备的物品");
            }

            var equipped = Game1.player.CurrentItem;
            _monitor.Log($"[V2] equip: 按槽位 {slot} 装备 -> {equipped?.DisplayName ?? "空"}", LogLevel.Trace);

            return ResultBuilder.Success(
                    cmd.Seq, cmd.ActionId,
                    new { slot, item_name = equipped?.DisplayName })
                .WithObservation($"已装备槽位 {slot} 的物品: {equipped?.DisplayName ?? "空"}");
        }

        // 按物品名称装备（支持全背包查找）
        if (TryGetString(cmd.Params, "item", out var itemName))
        {
            var result = _executor.EquipItemByName(itemName);
            if (!result.Success)
            {
                return ResultBuilder.Failure(
                    cmd.Seq, cmd.ActionId,
                    ResultBuilder.Errors.ItemNotFound,
                    $"背包中找不到物品: {itemName}");
            }

            var equipped = Game1.player.CurrentItem;
            _monitor.Log($"[V2] equip: 按名称装备 {itemName} -> {equipped?.DisplayName ?? "空"} (swapped={result.Swapped})", LogLevel.Trace);

            string observation;
            if (result.Swapped)
            {
                observation = $"已装备: {equipped?.DisplayName ?? itemName}（从背包槽位 {result.FromSlot} 交换到工具栏槽位 {result.ToSlot}，换出: {result.SwappedOutItemName}）";
            }
            else
            {
                observation = $"已装备: {equipped?.DisplayName ?? itemName}";
            }

            return ResultBuilder.Success(
                    cmd.Seq, cmd.ActionId,
                    new
                    {
                        item_name = equipped?.DisplayName,
                        slot = result.Slot,
                        swapped = result.Swapped,
                        from_slot = result.Swapped ? result.FromSlot : (int?)null,
                        swapped_out = result.Swapped ? result.SwappedOutItemName : null
                    })
                .WithObservation(observation);
        }

        return ResultBuilder.Failure(
            cmd.Seq, cmd.ActionId,
            ResultBuilder.Errors.InvalidParams,
            "equip 需要 item (物品名称) 或 slot (槽位编号) 参数");
    }

    private static bool TryGetString(Dictionary<string, object> p, string key, out string value)
    {
        value = "";
        if (!p.TryGetValue(key, out var raw)) return false;
        value = raw?.ToString() ?? "";
        return !string.IsNullOrEmpty(value);
    }

    private static bool TryGetInt(Dictionary<string, object> p, string key, out int value)
    {
        value = 0;
        if (!p.TryGetValue(key, out var raw)) return false;

        return raw switch
        {
            int i => (value = i) == i,
            long l when l >= int.MinValue && l <= int.MaxValue => (value = (int)l) == (int)l,
            double d when d >= int.MinValue && d <= int.MaxValue => (value = (int)d) == (int)d,
            string s => int.TryParse(s, NumberStyles.Integer, CultureInfo.InvariantCulture, out value),
            JsonElement e when e.ValueKind == JsonValueKind.Number => e.TryGetInt32(out value),
            JsonElement e when e.ValueKind == JsonValueKind.String =>
                int.TryParse(e.GetString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out value),
            _ => false,
        };
    }
}
