using System;
using System.Linq;
using System.Text.Json;
using StarCoPlay.Actions;
using StarCoPlay.Models;
using StardewModdingAPI;
using StardewValley;
using Microsoft.Xna.Framework;

namespace StarCoPlay.Bridge
{
    /// <summary>
    /// 命令处理器 — 轮询 commands.jsonl 新命令，解析并分发到
    /// ActionExecutor（单步动作）或 IBatchAction（批量操作），
    /// 执行后通过 EventEmitter 回写 cmd_result。
    /// </summary>
    public class CommandProcessor
    {
        private readonly IMonitor _monitor;
        private readonly BridgeManager _bridge;
        private readonly EventEmitter _emitter;
        private readonly ActionExecutor _executor;
        private readonly StateReader _stateReader;

        /// <summary>当前执行中的命令。</summary>
        private AgentCommand? _activeCommand;

        /// <summary>当前执行中的批量操作。</summary>
        private IBatchAction? _activeBatchAction;

        public CommandProcessor(
            IMonitor monitor,
            BridgeManager bridge,
            EventEmitter emitter,
            ActionExecutor executor,
            StateReader stateReader)
        {
            _monitor = monitor;
            _bridge = bridge;
            _emitter = emitter;
            _executor = executor;
            _stateReader = stateReader;
        }

        /// <summary>
        /// 主轮询方法，在 ModEntry.OnUpdateTicked 中每帧调用。
        /// </summary>
        public void Poll()
        {
            try
            {
                // 1. 如果有批量操作在执行 → Tick 推进
                if (_activeBatchAction != null)
                {
                    _activeBatchAction.Tick();

                    if (_activeBatchAction.IsComplete)
                    {
                        EmitResult(_activeCommand!, _activeBatchAction.IsSuccess,
                            _activeBatchAction.ResultMessage, _activeBatchAction.ErrorCode);
                        _activeBatchAction = null;
                        _activeCommand = null;
                    }
                    return;
                }

                // 2. 如果 ActionExecutor 正忙且有活跃命令 → 等待完成
                if (_executor.IsBusy && _activeCommand != null)
                {
                    return; // 下一帧继续检查
                }

                // ActionExecutor 刚完成一个单步命令
                if (!_executor.IsBusy && _activeCommand != null)
                {
                    // 单步命令完成（NavigateTo/UseToolAt 等通过状态机完成）
                    EmitResult(_activeCommand, true,
                        $"{_activeCommand.Action} 执行完成", null);
                    _activeCommand = null;
                }

                // 3. 读取新命令
                var commands = _bridge.ReadNewCommands();
                foreach (var cmd in commands)
                {
                    _monitor.Log($"CommandProcessor: 收到命令 [{cmd.CmdId}] {cmd.Action}", LogLevel.Debug);

                    // 如果已有活跃命令，拒绝新命令
                    if (_activeCommand != null || _activeBatchAction != null)
                    {
                        EmitResult(cmd, false, "Agent 正忙，请等待当前命令完成", "BUSY");
                        continue;
                    }

                    DispatchCommand(cmd);

                    // 如果刚分发了一个异步命令，停止处理后续命令
                    if (_activeCommand != null || _activeBatchAction != null)
                        break;
                }
            }
            catch (Exception ex)
            {
                _monitor.Log($"CommandProcessor.Poll 异常: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>分发命令到对应的执行方法。</summary>
        private void DispatchCommand(AgentCommand cmd)
        {
            try
            {
                switch (cmd.Action)
                {
                    case "navigate_to":
                        HandleNavigateTo(cmd);
                        break;

                    case "use_tool_on_tile":
                        HandleUseToolOnTile(cmd);
                        break;

                    case "equip_item":
                        HandleEquipItem(cmd);
                        break;

                    case "interact_with_tile":
                        HandleInteractWithTile(cmd);
                        break;

                    case "send_chat":
                        HandleSendChat(cmd);
                        break;

                    case "do_emote":
                        HandleDoEmote(cmd);
                        break;

                    case "show_hud":
                        HandleShowHUD(cmd);
                        break;

                    case "drop_item":
                        HandleDropItem(cmd);
                        break;

                    case "ship_item":
                        HandleShipItem(cmd);
                        break;

                    case "cancel":
                        HandleCancel(cmd);
                        break;

                    case "water_crops":
                        HandleWaterCrops(cmd);
                        break;

                    case "harvest_crops":
                        HandleHarvestCrops(cmd);
                        break;

                    case "go_to_bed":
                        HandleGoToBed(cmd);
                        break;

                    case "talk_to_npc":
                        HandleTalkToNpc(cmd);
                        break;

                    case "give_gift":
                        HandleGiveGift(cmd);
                        break;

                    case "get_active_dialog":
                        HandleGetActiveDialog(cmd);
                        break;

                    case "answer_dialog":
                        HandleAnswerDialog(cmd);
                        break;

                    case "peek_chest":
                        HandlePeekChest(cmd);
                        break;

                    case "store_in_chest":
                        HandleStoreInChest(cmd);
                        break;

                    case "take_from_chest":
                        HandleTakeFromChest(cmd);
                        break;

                    case "query":
                        HandleQuery(cmd);
                        break;

                    default:
                        EmitResult(cmd, false, $"未知动作: {cmd.Action}", "UNKNOWN_ACTION");
                        break;
                }
            }
            catch (Exception ex)
            {
                _monitor.Log($"CommandProcessor.DispatchCommand 异常: {ex.Message}", LogLevel.Error);
                EmitResult(cmd, false, $"命令执行异常: {ex.Message}", "DISPATCH_ERROR");
            }
        }

        /*────────────────────────────────────────────
         *  单步命令处理
         *────────────────────────────────────────────*/

        private void HandleNavigateTo(AgentCommand cmd)
        {
            // 如果有 location 参数 → 白名单传送（只允许传送到已知地图的安全坐标）
            if (cmd.Params.TryGetProperty("location", out var locElem))
            {
                string location = locElem.GetString() ?? "";

                // 检查目标地图是否在白名单中
                if (!MapData.IsKnownMap(location))
                {
                    EmitResult(cmd, false,
                        $"未知地图: {location}。使用 query get_location_info 查看可达地图列表",
                        "UNKNOWN_MAP");
                    return;
                }

                // 强制使用白名单中的安全坐标，忽略 AI 传入的 x/y
                var safeTarget = MapData.GetSafeWarpTarget(location);
                if (safeTarget == null)
                {
                    EmitResult(cmd, false, $"地图 {location} 无安全传送点", "WARP_FAILED");
                    return;
                }

                int safeX = safeTarget.Value.x;
                int safeY = safeTarget.Value.y;

                bool ok = _executor.WarpTo(location, safeX, safeY);
                EmitResult(cmd, ok,
                    ok ? $"正在传送到 {location} 安全点 ({safeX},{safeY})" : $"传送失败: {location}",
                    ok ? null : "WARP_FAILED");
                return;
            }

            // 同地图导航（纯寻路，不允许瞬移）
            int tileX = GetIntParam(cmd.Params, "x", -1);
            int tileY = GetIntParam(cmd.Params, "y", -1);

            if (tileX < 0 || tileY < 0)
            {
                EmitResult(cmd, false, "navigate_to 缺少 x/y 参数", "MISSING_PARAMS");
                return;
            }

            bool navOk = _executor.NavigateTo(new Point(tileX, tileY));
            if (!navOk)
            {
                EmitResult(cmd, false, $"无法导航到 ({tileX},{tileY})，目标可能不可达", "NAV_FAILED");
                return;
            }

            // 异步等待导航完成
            _activeCommand = cmd;
        }

        private void HandleUseToolOnTile(AgentCommand cmd)
        {
            int x = GetIntParam(cmd.Params, "x", -1);
            int y = GetIntParam(cmd.Params, "y", -1);

            if (x < 0 || y < 0)
            {
                EmitResult(cmd, false, "use_tool_on_tile 缺少 x/y 参数", "MISSING_PARAMS");
                return;
            }

            bool ok = _executor.UseToolAt(x, y);
            EmitResult(cmd, ok,
                ok ? $"在 ({x},{y}) 使用工具完成" : $"在 ({x},{y}) 使用工具失败",
                ok ? null : "TOOL_FAILED");
        }

        private void HandleEquipItem(AgentCommand cmd)
        {
            string itemName = GetStringParam(cmd.Params, "item_name", "");

            if (string.IsNullOrEmpty(itemName))
            {
                EmitResult(cmd, false, "equip_item 缺少 item_name 参数", "MISSING_PARAMS");
                return;
            }

            bool ok = _executor.EquipItemByName(itemName);
            EmitResult(cmd, ok,
                ok ? $"已装备 {itemName}" : $"找不到 {itemName}",
                ok ? null : "ITEM_NOT_FOUND");
        }

        private void HandleInteractWithTile(AgentCommand cmd)
        {
            int x = GetIntParam(cmd.Params, "x", -1);
            int y = GetIntParam(cmd.Params, "y", -1);

            if (x < 0 || y < 0)
            {
                EmitResult(cmd, false, "interact_with_tile 缺少 x/y 参数", "MISSING_PARAMS");
                return;
            }

            bool ok = _executor.InteractWithTile(x, y);
            EmitResult(cmd, ok,
                ok ? $"与 ({x},{y}) 交互完成" : $"({x},{y}) 处无可交互对象",
                ok ? null : "INTERACT_FAILED");
        }

        private void HandleSendChat(AgentCommand cmd)
        {
            string message = GetStringParam(cmd.Params, "message", "");

            if (string.IsNullOrEmpty(message))
            {
                EmitResult(cmd, false, "send_chat 缺少 message 参数", "MISSING_PARAMS");
                return;
            }

            _executor.SendChat(message);
            EmitResult(cmd, true, $"已发送聊天: {message}", null);
        }

        private void HandleShowHUD(AgentCommand cmd)
        {
            string message = GetStringParam(cmd.Params, "message", "");
            int type = GetIntParam(cmd.Params, "type", 0); // 0=普通 1=错误 2=成就 3=新配方 4=新任务

            if (string.IsNullOrEmpty(message))
            {
                EmitResult(cmd, false, "show_hud 缺少 message 参数", "MISSING_PARAMS");
                return;
            }

            _executor.ShowHUDMessage(message, type);
            EmitResult(cmd, true, $"已显示 HUD 消息: {message}", null);
        }

        private void HandleDoEmote(AgentCommand cmd)
        {
            string emote = GetStringParam(cmd.Params, "emote", "");

            if (string.IsNullOrEmpty(emote))
            {
                var available = string.Join(", ", _executor.GetAvailableEmotes().Keys);
                EmitResult(cmd, false, $"do_emote 缺少 emote 参数，可用: {available}", "MISSING_PARAMS");
                return;
            }

            bool ok = _executor.DoEmote(emote);
            if (ok)
                EmitResult(cmd, true, $"已播放表情: {emote}", null);
            else
            {
                var available = string.Join(", ", _executor.GetAvailableEmotes().Keys);
                EmitResult(cmd, false, $"未知表情: {emote}，可用: {available}", "INVALID_EMOTE");
            }
        }

        private void HandleDropItem(AgentCommand cmd)
        {
            string itemName = GetStringParam(cmd.Params, "item_name", "");
            int count = GetIntParam(cmd.Params, "count", -1);

            if (string.IsNullOrEmpty(itemName))
            {
                EmitResult(cmd, false, "drop_item 缺少 item_name 参数", "MISSING_PARAMS");
                return;
            }

            bool ok = _executor.DropItem(itemName, count);
            EmitResult(cmd, ok,
                ok ? $"已丢弃 {itemName}" : $"找不到 {itemName}",
                ok ? null : "ITEM_NOT_FOUND");
        }

        private void HandleShipItem(AgentCommand cmd)
        {
            string itemName = GetStringParam(cmd.Params, "item_name", "");
            int count = GetIntParam(cmd.Params, "count", -1);

            if (string.IsNullOrEmpty(itemName))
            {
                EmitResult(cmd, false, "ship_item 缺少 item_name 参数", "MISSING_PARAMS");
                return;
            }

            bool ok = _executor.ShipItem(itemName, count);
            EmitResult(cmd, ok,
                ok ? $"已放入出货箱 {itemName}" : $"找不到 {itemName}",
                ok ? null : "ITEM_NOT_FOUND");
        }

        private void HandleCancel(AgentCommand cmd)
        {
            // 取消批量操作
            if (_activeBatchAction != null)
            {
                _activeBatchAction.Cancel();
                _activeBatchAction = null;
                _activeCommand = null;
            }

            _executor.CancelCurrent();
            EmitResult(cmd, true, "已取消当前动作", null);
        }

        /*────────────────────────────────────────────
         *  批量命令处理
         *────────────────────────────────────────────*/

        private void HandleWaterCrops(AgentCommand cmd)
        {
            int maxCount = GetIntParam(cmd.Params, "max_count", 999);

            var action = new BatchWateringAction(_monitor, _executor, _stateReader, maxCount);
            action.Start();

            if (action.IsComplete)
            {
                // 立即完成（无目标或启动失败）
                EmitResult(cmd, action.IsSuccess, action.ResultMessage, action.ErrorCode);
            }
            else
            {
                _activeCommand = cmd;
                _activeBatchAction = action;
            }
        }

        private void HandleHarvestCrops(AgentCommand cmd)
        {
            var action = new BatchHarvestAction(_monitor, _executor, _stateReader);
            action.Start();

            if (action.IsComplete)
            {
                EmitResult(cmd, action.IsSuccess, action.ResultMessage, action.ErrorCode);
            }
            else
            {
                _activeCommand = cmd;
                _activeBatchAction = action;
            }
        }

        private void HandleGoToBed(AgentCommand cmd)
        {
            var action = new GoToBedAction(_monitor, _executor);
            action.Start();

            if (action.IsComplete)
            {
                EmitResult(cmd, action.IsSuccess, action.ResultMessage, action.ErrorCode);
            }
            else
            {
                _activeCommand = cmd;
                _activeBatchAction = action;
            }
        }

        /*────────────────────────────────────────────
         *  NPC 交互命令
         *────────────────────────────────────────────*/

        private void HandleTalkToNpc(AgentCommand cmd)
        {
            string npcName = GetStringParam(cmd.Params, "npc_name", "");
            if (string.IsNullOrEmpty(npcName))
            {
                EmitResult(cmd, false, "talk_to_npc 缺少 npc_name 参数", "MISSING_PARAMS");
                return;
            }

            var location = Game1.currentLocation;
            if (location == null)
            {
                EmitResult(cmd, false, "当前无有效地图", "INVALID_STATE");
                return;
            }

            // 在当前地图查找 NPC（同时匹配 Name 和 displayName）
            var npc = location.characters.FirstOrDefault(
                c => c.Name.Equals(npcName, StringComparison.OrdinalIgnoreCase)
                  || c.displayName.Equals(npcName, StringComparison.OrdinalIgnoreCase));

            // 回退：通过全局查找（NPC 可能不在 characters 列表但在地图上）
            if (npc == null)
            {
                var globalNpc = Game1.getCharacterFromName(npcName);
                if (globalNpc != null && globalNpc.currentLocation == location)
                    npc = globalNpc;
            }

            if (npc == null)
            {
                // 列出当前地图的 NPC 辅助诊断
                var available = location.characters.Select(c => c.Name).ToList();
                _monitor.Log($"HandleTalkToNpc: 当前地图 {location.Name} 的 NPC: [{string.Join(", ", available)}]", LogLevel.Debug);
                EmitResult(cmd, false,
                    $"当前地图 {location.Name} 未找到 NPC: {npcName}（地图上有: {string.Join(", ", available)}）",
                    "NPC_NOT_FOUND");
                return;
            }

            // 检查距离（需要在交互范围内）
            float distance = Vector2.Distance(Game1.player.Tile, npc.Tile);
            if (distance > 2f)
            {
                EmitResult(cmd, false,
                    $"{npcName} 距离太远 ({distance:F1} tiles)，请先使用 navigate_to 靠近",
                    "OUT_OF_RANGE");
                return;
            }

            // 面朝 NPC 并触发对话
            Game1.player.faceGeneralDirection(npc.Position);
            npc.checkAction(Game1.player, location);

            EmitResult(cmd, true, $"已与 {npcName} 对话", null);
        }

        private void HandleGiveGift(AgentCommand cmd)
        {
            string npcName = GetStringParam(cmd.Params, "npc_name", "");
            string itemName = GetStringParam(cmd.Params, "item_name", "");
            if (string.IsNullOrEmpty(npcName) || string.IsNullOrEmpty(itemName))
            {
                EmitResult(cmd, false, "give_gift 缺少 npc_name 或 item_name 参数", "MISSING_PARAMS");
                return;
            }

            var location = Game1.currentLocation;
            if (location == null)
            {
                EmitResult(cmd, false, "当前无有效地图", "INVALID_STATE");
                return;
            }

            // 查找 NPC（同时匹配 Name 和 displayName）
            var npc = location.characters.FirstOrDefault(
                c => c.Name.Equals(npcName, StringComparison.OrdinalIgnoreCase)
                  || c.displayName.Equals(npcName, StringComparison.OrdinalIgnoreCase));

            // 回退：全局查找
            if (npc == null)
            {
                var globalNpc = Game1.getCharacterFromName(npcName);
                if (globalNpc != null && globalNpc.currentLocation == location)
                    npc = globalNpc;
            }

            if (npc == null)
            {
                EmitResult(cmd, false, $"当前地图未找到 NPC: {npcName}", "NPC_NOT_FOUND");
                return;
            }

            // 检查距离
            float distance = Vector2.Distance(Game1.player.Tile, npc.Tile);
            if (distance > 2f)
            {
                EmitResult(cmd, false,
                    $"{npcName} 距离太远 ({distance:F1} tiles)，请先靠近",
                    "OUT_OF_RANGE");
                return;
            }

            // 在背包中查找物品
            var player = Game1.player;
            int itemSlot = -1;
            StardewValley.Object? giftItem = null;
            for (int i = 0; i < player.Items.Count; i++)
            {
                if (player.Items[i] is StardewValley.Object obj &&
                    (obj.DisplayName.Equals(itemName, StringComparison.OrdinalIgnoreCase) ||
                     obj.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase)))
                {
                    giftItem = obj;
                    itemSlot = i;
                    break;
                }
            }

            if (giftItem == null)
            {
                EmitResult(cmd, false, $"背包中未找到物品: {itemName}", "ITEM_NOT_FOUND");
                return;
            }

            // 装备该物品并送礼
            player.CurrentToolIndex = itemSlot;
            Game1.player.faceGeneralDirection(npc.Position);

            // 调用 NPC 收礼逻辑
            bool giftAccepted = npc.tryToReceiveActiveObject(player);
            if (giftAccepted)
            {
                EmitResult(cmd, true, $"已将 {giftItem.DisplayName} 送给 {npcName}", null);
            }
            else
            {
                EmitResult(cmd, false,
                    $"{npcName} 未接受礼物 {giftItem.DisplayName}（可能已达每周上限或其他限制）",
                    "ACTION_FAILED");
            }
        }

        /*────────────────────────────────────────────
         *  对话框命令
         *────────────────────────────────────────────*/

        private void HandleGetActiveDialog(AgentCommand cmd)
        {
            var dialogInfo = _executor.GetActiveDialog();
            if (dialogInfo == null)
            {
                EmitResult(cmd, false, "当前没有活跃的对话框", "NO_DIALOG");
                return;
            }

            _emitter.EmitCommandResult(cmd.CmdId, true, "获取对话框信息成功", null, dialogInfo);
            _monitor.Log($"CommandProcessor: [{cmd.CmdId}] get_active_dialog 完成", LogLevel.Debug);
        }

        private void HandleAnswerDialog(AgentCommand cmd)
        {
            int choice = GetIntParam(cmd.Params, "choice", -1);

            var (success, message, errorCode) = _executor.AnswerDialog(choice);
            EmitResult(cmd, success, message, errorCode);
        }

        /*────────────────────────────────────────────
         *  箱子直接操作命令（绕过 UI）
         *────────────────────────────────────────────*/

        private void HandlePeekChest(AgentCommand cmd)
        {
            int x = GetIntParam(cmd.Params, "x", -1);
            int y = GetIntParam(cmd.Params, "y", -1);

            if (x < 0 || y < 0)
            {
                EmitResult(cmd, false, "peek_chest 缺少 x/y 参数", "MISSING_PARAMS");
                return;
            }

            var items = _executor.PeekChest(x, y);
            if (items == null)
            {
                EmitResult(cmd, false, $"({x},{y}) 处无箱子", "NO_CHEST");
                return;
            }

            _emitter.EmitCommandResult(cmd.CmdId, true,
                $"箱子 ({x},{y}) 含 {items.Count} 种物品", null, items);
            _monitor.Log($"CommandProcessor: [{cmd.CmdId}] peek_chest ({x},{y}) → {items.Count} 种物品", LogLevel.Debug);
        }

        private void HandleStoreInChest(AgentCommand cmd)
        {
            int x = GetIntParam(cmd.Params, "x", -1);
            int y = GetIntParam(cmd.Params, "y", -1);
            string itemName = GetStringParam(cmd.Params, "item_name", "");
            int count = GetIntParam(cmd.Params, "count", -1);

            if (x < 0 || y < 0 || string.IsNullOrEmpty(itemName))
            {
                EmitResult(cmd, false, "store_in_chest 缺少 x/y/item_name 参数", "MISSING_PARAMS");
                return;
            }

            var (success, errorCode) = _executor.StoreInChest(x, y, itemName, count);
            EmitResult(cmd, success,
                success ? $"已将 {itemName} 存入箱子 ({x},{y})" : $"存入失败: {errorCode}",
                errorCode);
        }

        private void HandleTakeFromChest(AgentCommand cmd)
        {
            int x = GetIntParam(cmd.Params, "x", -1);
            int y = GetIntParam(cmd.Params, "y", -1);
            string itemName = GetStringParam(cmd.Params, "item_name", "");
            int count = GetIntParam(cmd.Params, "count", -1);

            if (x < 0 || y < 0 || string.IsNullOrEmpty(itemName))
            {
                EmitResult(cmd, false, "take_from_chest 缺少 x/y/item_name 参数", "MISSING_PARAMS");
                return;
            }

            var (success, errorCode) = _executor.TakeFromChest(x, y, itemName, count);
            EmitResult(cmd, success,
                success ? $"已从箱子 ({x},{y}) 取出 {itemName}" : $"取出失败: {errorCode}",
                errorCode);
        }

        /*────────────────────────────────────────────
         *  查询命令
         *────────────────────────────────────────────*/

        private void HandleQuery(AgentCommand cmd)
        {
            string tool = GetStringParam(cmd.Params, "tool", "");
            if (string.IsNullOrEmpty(tool))
            {
                EmitResult(cmd, false, "query 缺少 tool 参数", "MISSING_PARAMS");
                return;
            }

            bool detail = GetBoolParam(cmd.Params, "detail", false);

            try
            {
                object? result = tool switch
                {
                    "get_world_state" => _stateReader.GetWorldState(),
                    "get_player_state" => _stateReader.GetPlayerState(),
                    "get_inventory" => _stateReader.GetInventory(),
                    "get_farm_summary" => _stateReader.GetFarmSummary(),
                    "get_available_actions" => _stateReader.GetAvailableActions(),
                    "get_location_info" => _stateReader.GetLocationInfo(),
                    "get_map_objects" => _stateReader.GetMapObjects(detail),
                    "get_walkable_area" => _stateReader.GetWalkableArea(),
                    "get_map_exits" => _stateReader.GetMapExits(),
                    _ => null
                };

                if (result == null)
                {
                    EmitResult(cmd, false, $"未知查询工具: {tool}", "UNKNOWN_TOOL");
                    return;
                }

                _emitter.EmitCommandResult(cmd.CmdId, true, $"查询 {tool} 完成", null, result);
                _monitor.Log($"CommandProcessor: [{cmd.CmdId}] query {tool} 完成", LogLevel.Debug);
            }
            catch (Exception ex)
            {
                EmitResult(cmd, false, $"查询 {tool} 失败: {ex.Message}", "QUERY_ERROR");
            }
        }

        /*────────────────────────────────────────────
         *  辅助方法
         *────────────────────────────────────────────*/

        private void EmitResult(AgentCommand cmd, bool success, string message, string? errorCode)
        {
            _emitter.EmitCommandResult(cmd.CmdId, success, message, errorCode);
            _monitor.Log($"CommandProcessor: [{cmd.CmdId}] {cmd.Action} → success={success} {message}", LogLevel.Debug);
        }

        /// <summary>从 JsonElement 安全提取 int 参数。</summary>
        private static int GetIntParam(JsonElement paramsElem, string name, int defaultValue)
        {
            try
            {
                if (paramsElem.ValueKind == JsonValueKind.Object &&
                    paramsElem.TryGetProperty(name, out var prop))
                {
                    if (prop.ValueKind == JsonValueKind.Number)
                        return prop.GetInt32();
                    if (prop.ValueKind == JsonValueKind.String &&
                        int.TryParse(prop.GetString(), out int val))
                        return val;
                }
            }
            catch
            {
                // 解析失败返回默认值
            }
            return defaultValue;
        }

        /// <summary>从 JsonElement 安全提取 string 参数。</summary>
        private static string GetStringParam(JsonElement paramsElem, string name, string defaultValue)
        {
            try
            {
                if (paramsElem.ValueKind == JsonValueKind.Object &&
                    paramsElem.TryGetProperty(name, out var prop))
                {
                    return prop.GetString() ?? defaultValue;
                }
            }
            catch
            {
                // 解析失败返回默认值
            }
            return defaultValue;
        }

        /// <summary>从 JsonElement 安全提取 bool 参数。</summary>
        private static bool GetBoolParam(JsonElement paramsElem, string name, bool defaultValue)
        {
            try
            {
                if (paramsElem.ValueKind == JsonValueKind.Object &&
                    paramsElem.TryGetProperty(name, out var prop))
                {
                    if (prop.ValueKind == JsonValueKind.True) return true;
                    if (prop.ValueKind == JsonValueKind.False) return false;
                    if (prop.ValueKind == JsonValueKind.String)
                    {
                        var str = prop.GetString()?.ToLowerInvariant();
                        if (str == "true" || str == "1") return true;
                        if (str == "false" || str == "0") return false;
                    }
                }
            }
            catch
            {
                // 解析失败返回默认值
            }
            return defaultValue;
        }
    }
}
