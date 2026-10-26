using System;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.V2.Compound;
using StarCoPlay.GameServices.SimulatedInput;
using StarCoPlay.V2;
using StarCoPlay.V2.Models;

namespace StarCoPlay.Capabilities.ExecutionAtoms;

/// <summary>
/// L1 V2 — equip 命令：按数字键切换工具栏装备。
///
/// 与旧 <see cref="EquipHandler"/> 的区别：
/// - 旧版直接通过 <see cref="ActionExecutor.EquipItem"/> /
///   <see cref="ActionExecutor.EquipItemByName"/> 修改 player.CurrentToolIndex /
///   交换 player.Items，绕过键位，不会触发任何切换动画。
/// - V2 版优先使用 InputCombo.EquipSlot(slot)，通过 InputBridge 入队
///   D1..D9/D0/OemMinus/OemPlus 数字键，让游戏自己处理切换（含动画）。
/// - 兜底：当 item_name 在工具栏可见行（slot 1-12）找不到时，
///   fallback 到 ActionExecutor.EquipItemByName（直接 API + 背包深层交换），
///   保留全背包查找能力。
///
/// 入参契约（slot 优先于 item_name）：
///   slot:      int    1-12（工具栏栏位编号）
///   item_name: string 物品名称（DisplayName/Name 子串匹配，不区分大小写）
///   name:      string 兼容别名，等价于 item_name
/// </summary>
public class EquipHandler
{
    private readonly InputCombo _combo;
    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;

    public EquipHandler(InputCombo combo, ActionExecutor executor, IMonitor monitor)
    {
        _combo = combo;
        _executor = executor;
        _monitor = monitor;
    }

    public V2Result? Handle(V2Command cmd)
    {
        // 0. cmd.Params 防御（极少数路径可能传 null）
        var p = cmd.Params ?? new System.Collections.Generic.Dictionary<string, object>();

        // 注：原"失焦防呆（IsActive）"已在 Stage A 切换到 IInputSimulator 后移除。
        // simulator 路径直接覆写 SDV 内部 ref bool，失焦也能工作；防呆变成 anti-feature。
        // equip 的 slot 模式走数字键 D1-D9（SMAPI Press 路径），失焦时无法工作——
        // 但应由 InputBridge 自身行为反映（不是这里防呆的责任）。

        // 1. 优先 slot 模式（直接按数字键）
        if (CompoundUtils.TryGetInt(p, "slot", out var slot))
        {
            if (slot < 1 || slot > 12)
            {
                return ResultBuilder.Failure(
                    cmd.Seq, cmd.ActionId,
                    ResultBuilder.Errors.InvalidParams,
                    $"slot 必须在 1-12 之间，实际: {slot}");
            }

            // M4：slot 模式入队前检查物品栏是否真的有物品（避免按了键但是空槽）
            int slotIdx = slot - 1; // 转 0-based 数组索引
            if (slotIdx >= Game1.player.Items.Count || Game1.player.Items[slotIdx] == null)
            {
                return ResultBuilder.Failure(
                    cmd.Seq, cmd.ActionId,
                    ResultBuilder.Errors.ItemNotFound,
                    $"slot={slot} 为空槽位，无可装备物品");
            }

            // 直接设 CurrentToolIndex（最早期 direct API 方案）：聚焦/失焦行为统一，
            // 天然支持多行 hotbar（slot > 12 跨行选中，不需要先按 Tab 切换 hotbar 行）。
            // 不入队 D 数字键 keypress，避免 SDV hotbar 切换路径在失焦下的特殊行为问题。
            _monitor.Log(
                $"[ExecutionAtom] equip: set CurrentToolIndex={slotIdx}（direct API）",
                LogLevel.Debug);
            Game1.player.CurrentToolIndex = slotIdx;

            return ResultBuilder.Success(
                    cmd.Seq, cmd.ActionId,
                    new { slot, method = "direct_api" })
                .WithObservation($"已切到工具栏 slot={slot}（{Game1.player.CurrentItem?.DisplayName ?? "空"}）");
        }

        // 3. item_name 模式：先查工具栏可见行，命中则按数字键，否则 fallback 到旧 API
        if (CompoundUtils.TryGetString(p, "item_name", out var itemName)
            || CompoundUtils.TryGetString(p, "name", out itemName))
        {
            int foundSlot = FindItemSlot(itemName);
            if (foundSlot >= 1 && foundSlot <= 12)
            {
                _monitor.Log(
                    $"[ExecutionAtom] equip: found {itemName} in toolbar slot={foundSlot}, tap number key",
                    LogLevel.Debug);
                _combo.EquipSlot(foundSlot);

                return ResultBuilder.Success(
                        cmd.Seq, cmd.ActionId,
                        new { item_name = itemName, slot = foundSlot, method = "keypress" })
                    .WithObservation($"已入队 equip item={itemName} slot={foundSlot}");
            }

            // 4. Fallback：物品不在工具栏可见行（可能在背包深层 slot 12-35），
            //    用旧 API 直接装备（含背包→工具栏交换逻辑）
            _monitor.Log(
                $"[ExecutionAtom] equip: {itemName} not in toolbar, fallback to direct API",
                LogLevel.Debug);
            var result = _executor.EquipItemByName(itemName);
            if (result.Success)
            {
                // ActionExecutor.EquipItemByName 返回的 Slot/FromSlot/ToSlot 全部是 0-based（内部数组下标）。
                // V2 toolbar 命中路径的 slot 字段是 1-based（FindItemSlot 返回 i+1，与游戏内数字键 1..0 对齐）。
                // 为保持 V2 输出契约一致，fallback 路径所有 slot 字段都 +1 转为 1-based。
                int slotOneBased = result.Slot + 1;
                int? fromSlotOneBased = result.Swapped ? result.FromSlot + 1 : (int?)null;
                int? toSlotOneBased = result.Swapped ? result.ToSlot + 1 : (int?)null;

                string observation = result.Swapped
                    ? $"已装备 {itemName}（从背包槽位 {fromSlotOneBased} 交换到工具栏槽位 {toSlotOneBased}，换出: {result.SwappedOutItemName}，直接 API）"
                    : $"已装备 {itemName}（工具栏槽位 {slotOneBased}，直接 API）";

                return ResultBuilder.Success(
                        cmd.Seq, cmd.ActionId,
                        new
                        {
                            item_name = result.ItemName ?? itemName,
                            slot = slotOneBased,
                            method = "direct_api",
                            swapped = result.Swapped,
                            from_slot = fromSlotOneBased,
                            to_slot = toSlotOneBased,
                            swapped_out = result.Swapped ? result.SwappedOutItemName : null
                        })
                    .WithObservation(observation);
            }

            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ItemNotFound,
                $"背包中找不到物品: {itemName}");
        }

        return ResultBuilder.Failure(
            cmd.Seq, cmd.ActionId,
            ResultBuilder.Errors.InvalidParams,
            "equip 需要 slot (1-12) 或 item_name 参数");
    }

    /// <summary>
    /// 在玩家工具栏（前 12 格，0-based 0..11）查找物品。
    /// 按 DisplayName / Name 子串匹配，不区分大小写。
    /// 返回 1-based slot（1..12），未找到返回 -1。
    /// </summary>
    private static int FindItemSlot(string itemName)
    {
        var items = Game1.player.Items;
        int max = Math.Min(12, items.Count);
        for (int i = 0; i < max; i++)
        {
            var item = items[i];
            if (item == null) continue;

            bool nameHit = !string.IsNullOrEmpty(item.Name)
                && item.Name.IndexOf(itemName, StringComparison.OrdinalIgnoreCase) >= 0;
            bool displayHit = !string.IsNullOrEmpty(item.DisplayName)
                && item.DisplayName.IndexOf(itemName, StringComparison.OrdinalIgnoreCase) >= 0;

            if (nameHit || displayHit)
            {
                return i + 1; // 1-based
            }
        }
        return -1;
    }

    // 参数解析下沉到 CompoundUtils.TryGetInt / TryGetString（C1 修复，避免 5 处独立实现漂移）。
}
