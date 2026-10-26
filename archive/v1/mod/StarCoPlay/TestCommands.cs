using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.Bridge;

namespace StarCoPlay
{
    /// <summary>
    /// SMAPI 控制台测试命令 — 远程协作测试的核心交互工具。
    /// 在 SMAPI 控制台输入命令 → 观察游戏画面 = 可视化验证。
    /// </summary>
    public class TestCommands
    {
        private readonly IMonitor _monitor;
        private readonly StateReader _state;
        private readonly ActionExecutor _actions;
        private readonly DebugReporter _reporter;
        private readonly EventEmitter? _emitter;

        public TestCommands(IModHelper helper, IMonitor monitor, StateReader state, ActionExecutor actions, DebugReporter reporter, EventEmitter? emitter = null)
        {
            _monitor = monitor;
            _state = state;
            _actions = actions;
            _reporter = reporter;
            _emitter = emitter;

            // 注册全部命令
            helper.ConsoleCommands.Add("agent", "向 AI Agent 下达自然语言指令 (用法: agent <你的指令>)", CmdDirective);
            helper.ConsoleCommands.Add("agent_status", "打印完整状态（世界/玩家/背包）", CmdStatus);
            helper.ConsoleCommands.Add("agent_npcs", "列出当前地图所有 NPC", CmdNpcs);
            helper.ConsoleCommands.Add("agent_objects", "列出附近物品/作物", CmdObjects);
            helper.ConsoleCommands.Add("agent_goto", "寻路移动到指定瓦片 (用法: agent_goto <x> <y>)", CmdGoto);
            helper.ConsoleCommands.Add("agent_goto_npc", "寻路到指定 NPC 面前 (用法: agent_goto_npc <name>)", CmdGotoNpc);
            helper.ConsoleCommands.Add("agent_warp", "传送到指定地图 (用法: agent_warp <location> [x] [y])", CmdWarp);
            helper.ConsoleCommands.Add("agent_interact", "与瓦片上物品/NPC 交互 (用法: agent_interact <x> <y>)", CmdInteract);
            helper.ConsoleCommands.Add("agent_equip", "装备指定物品 (用法: agent_equip <name>)", CmdEquip);
            helper.ConsoleCommands.Add("agent_farm", "打印农场概况", CmdFarm);
            helper.ConsoleCommands.Add("agent_usetool", "对瓦片使用当前工具 (用法: agent_usetool <x> <y>)", CmdUseTool);
            helper.ConsoleCommands.Add("agent_drop", "丢弃物品 (用法: agent_drop <name> [count])", CmdDrop);
            helper.ConsoleCommands.Add("agent_ship", "��入出货箱 (用法: agent_ship <name> [count])", CmdShip);
            helper.ConsoleCommands.Add("agent_tool", "查询工具状态 (用法: agent_tool <name>)", CmdToolState);
            helper.ConsoleCommands.Add("agent_value", "查询物品价值 (用法: agent_value <name>)", CmdItemValue);
            helper.ConsoleCommands.Add("agent_emote", "播放表情 (用法: agent_emote <name|index>)", CmdEmote);
            helper.ConsoleCommands.Add("agent_cancel", "取消当前动作", CmdCancel);
            helper.ConsoleCommands.Add("agent_feedback", "记录人工反馈 (用法: agent_feedback <text>)", CmdFeedback);
            helper.ConsoleCommands.Add("agent_report", "立即生成测试报告", CmdReport);
        }

        /*────────────────────────────────────────────
         *  agent_status — 完整状态
         *────────────────────────────────────────────*/
        private void CmdStatus(string command, string[] args)
        {
            if (!RequireWorld()) return;

            var world = _state.GetWorldState();
            var player = _state.GetPlayerState();
            var inventory = _state.GetInventory();
            var availability = _state.GetAvailableActions();

            _monitor.Log("=== 世界状态 ===", LogLevel.Info);
            _monitor.Log($"  时间: {world.Season} Y{world.Year} D{world.Day} {FormatTime(world.Time)}", LogLevel.Info);
            _monitor.Log($"  天气: {world.Weather} | 节日: {(world.IsFestivalDay ? "是" : "否")}", LogLevel.Info);

            _monitor.Log("=== 玩家状态 ===", LogLevel.Info);
            _monitor.Log($"  位置: {player.Location} ({player.TileX},{player.TileY})", LogLevel.Info);
            _monitor.Log($"  体力: {player.Energy}/{player.MaxEnergy} | 生命: {player.Health}/{player.MaxHealth}", LogLevel.Info);
            _monitor.Log($"  金币: {player.Gold} | 手持: {player.CurrentItemName ?? "空"}", LogLevel.Info);
            _monitor.Log($"  技能: 农{player.FarmingLevel} 矿{player.MiningLevel} 采{player.ForagingLevel} 钓{player.FishingLevel} 战{player.CombatLevel}", LogLevel.Info);

            _monitor.Log("=== 背包 ===", LogLevel.Info);
            _monitor.Log($"  使用: {inventory.SlotsUsed}/{inventory.SlotsTotal}", LogLevel.Info);
            foreach (var item in inventory.Items.Take(12))
                _monitor.Log($"  [{item.Slot}] {item.Name} x{item.Stack} (Q{item.Quality})", LogLevel.Info);
            if (inventory.SlotsUsed > 12)
                _monitor.Log($"  ...还有 {inventory.SlotsUsed - 12} 个物品", LogLevel.Info);

            _monitor.Log("=== 可用动作 ===", LogLevel.Info);
            _monitor.Log($"  可移动: {availability.CanMove} | 空闲: {availability.IsPlayerFree} | 菜单: {availability.IsMenuOpen} | 对话: {availability.IsInDialogue}", LogLevel.Info);
            _monitor.Log($"  Agent 状态: {(_actions.IsBusy ? "忙碌" : "空闲")}", LogLevel.Info);
        }

        /*────────────────────────────────────────────
         *  agent_npcs — NPC 列表
         *────────────────────────────────────────────*/
        private void CmdNpcs(string command, string[] args)
        {
            if (!RequireWorld()) return;

            var npcs = _state.GetNearbyNpcs(radius: 999);
            _monitor.Log($"=== 当前地图 NPC ({npcs.Count}) ===", LogLevel.Info);
            foreach (var npc in npcs)
            {
                string talked = npc.TalkedToday ? "✓" : "✗";
                _monitor.Log($"  {npc.DisplayName} ({npc.Name}) @ ({npc.TileX},{npc.TileY}) | {npc.Hearts}♥ | 今日对话: {talked}", LogLevel.Info);
            }
            if (npcs.Count == 0)
                _monitor.Log("  (当前地图无 NPC)", LogLevel.Info);
        }

        /*────────────────────────────────────────────
         *  agent_objects — 附近物品
         *────────────────────────────────────────────*/
        private void CmdObjects(string command, string[] args)
        {
            if (!RequireWorld()) return;

            int radius = 10;
            if (args.Length > 0 && int.TryParse(args[0], out int r))
                radius = r;

            var objects = _state.GetNearbyObjects(radius);
            _monitor.Log($"=== 附近物品 (半径 {radius}) ({objects.Count}) ===", LogLevel.Info);
            foreach (var obj in objects)
            {
                string extra = "";
                if (obj.Category == "terrain")
                {
                    extra = obj.IsWatered ? " [已浇水]" : " [未浇水]";
                    if (obj.IsHarvestable == true) extra += " [可收获]";
                }
                _monitor.Log($"  ({obj.TileX},{obj.TileY}) {obj.Name} [{obj.Category}]{extra}", LogLevel.Info);
            }
            if (objects.Count == 0)
                _monitor.Log("  (附近无物品)", LogLevel.Info);
        }

        /*────────────────────────────────────────────
         *  agent_goto — 寻路移动
         *────────────────────────────────────────────*/
        private void CmdGoto(string command, string[] args)
        {
            if (!RequireWorld()) return;

            if (args.Length < 2 || !int.TryParse(args[0], out int x) || !int.TryParse(args[1], out int y))
            {
                _monitor.Log("用法: agent_goto <x> <y>", LogLevel.Warn);
                return;
            }

            bool success = _actions.NavigateTo(new Point(x, y));
            if (!success)
                _monitor.Log($"导航到 ({x},{y}) 失败，请检查目标是否可达", LogLevel.Warn);
        }

        /*────────────────────────────────────────────
         *  agent_goto_npc — 寻路到 NPC
         *────────────────────────────────────────────*/
        private void CmdGotoNpc(string command, string[] args)
        {
            if (!RequireWorld()) return;

            if (args.Length < 1)
            {
                _monitor.Log("用法: agent_goto_npc <name>", LogLevel.Warn);
                return;
            }

            string npcName = string.Join(" ", args);
            var location = Game1.currentLocation;
            if (location == null) return;

            // 查找 NPC
            var npc = location.characters.FirstOrDefault(c =>
                c.Name.ToLower() == npcName.ToLower() ||
                c.displayName.ToLower() == npcName.ToLower());

            if (npc == null)
            {
                _monitor.Log($"当前地图找不到 NPC '{npcName}'", LogLevel.Warn);
                // 列出可用的 NPC
                var available = location.characters.Select(c => c.Name);
                _monitor.Log($"当前地图的 NPC: {string.Join(", ", available)}", LogLevel.Debug);
                return;
            }

            // 导航到 NPC 面前（NPC 瓦片的南侧一格）
            var target = new Point((int)npc.Tile.X, (int)npc.Tile.Y + 1);
            _monitor.Log($"导航到 {npc.displayName} 面前 ({target.X},{target.Y})...", LogLevel.Debug);
            _actions.NavigateTo(target);
        }

        /*────────────────────────────────────────────
         *  agent_warp — 传送
         *────────────────────────────────────────────*/
        private void CmdWarp(string command, string[] args)
        {
            if (!RequireWorld()) return;

            if (args.Length < 1)
            {
                _monitor.Log("用法: agent_warp <location> [x] [y]", LogLevel.Warn);
                return;
            }

            string locationName = args[0];
            int x = 64, y = 15; // 默认坐标

            if (args.Length >= 3)
            {
                if (int.TryParse(args[1], out int parsedX)) x = parsedX;
                if (int.TryParse(args[2], out int parsedY)) y = parsedY;
            }

            bool success = _actions.WarpTo(locationName, x, y);
            if (!success)
                _monitor.Log($"传送到 {locationName} 失败", LogLevel.Warn);
        }

        /*────────────────────────────────────────────
         *  agent_interact — 交互
         *────────────────────────────────────────────*/
        private void CmdInteract(string command, string[] args)
        {
            if (!RequireWorld()) return;

            if (args.Length < 2 || !int.TryParse(args[0], out int x) || !int.TryParse(args[1], out int y))
            {
                _monitor.Log("用法: agent_interact <x> <y>", LogLevel.Warn);
                return;
            }

            _actions.InteractWithTile(x, y);
        }

        /*────────────────────────────────────────────
         *  agent_equip — 装备物品
         *────────────────────────────────────────────*/
        private void CmdEquip(string command, string[] args)
        {
            if (!RequireWorld()) return;

            if (args.Length < 1)
            {
                _monitor.Log("用法: agent_equip <name>", LogLevel.Warn);
                return;
            }

            string itemName = string.Join(" ", args);
            bool success = _actions.EquipItemByName(itemName);
            if (!success)
            {
                // 列出快捷栏物品供参考
                var inventory = _state.GetInventory();
                var hotbar = inventory.Items.Where(i => i.Slot < 12).Select(i => i.Name);
                _monitor.Log($"快捷栏物品: {string.Join(", ", hotbar)}", LogLevel.Debug);
            }
        }

        /*────────────────────────────────────────────
         *  agent_farm — 农场概况
         *────────────────────────────────────────────*/
        private void CmdFarm(string command, string[] args)
        {
            if (!RequireWorld()) return;

            var summary = _state.GetFarmSummary();
            if (summary == null)
            {
                _monitor.Log("无法获取农场信息", LogLevel.Warn);
                return;
            }

            _monitor.Log("=== 农场概况 ===", LogLevel.Info);
            _monitor.Log($"  作物: {summary.CropCount} 株", LogLevel.Info);
            _monitor.Log($"  未浇水: {summary.UnwateredCount} 株", LogLevel.Info);
            _monitor.Log($"  可收获: {summary.HarvestableCount} 株", LogLevel.Info);
        }

        /*────────────────────────────────────────────
         *  agent_usetool — 使用工具
         *────────────────────────────────────────────*/
        private void CmdUseTool(string command, string[] args)
        {
            if (!RequireWorld()) return;

            if (args.Length < 2 || !int.TryParse(args[0], out int x) || !int.TryParse(args[1], out int y))
            {
                _monitor.Log("用法: agent_usetool <x> <y>", LogLevel.Warn);
                return;
            }

            bool success = _actions.UseToolAt(x, y);
            if (!success)
                _monitor.Log($"工具使用失败 @ ({x},{y})", LogLevel.Warn);
        }

        /*────────────────────────────────────────────
         *  agent_drop — 丢弃物品
         *────────────────────────────────────────────*/
        private void CmdDrop(string command, string[] args)
        {
            if (!RequireWorld()) return;

            if (args.Length < 1)
            {
                _monitor.Log("用法: agent_drop <name> [count]", LogLevel.Warn);
                return;
            }

            int count = -1;
            string name;
            if (args.Length >= 2 && int.TryParse(args[^1], out int c))
            {
                count = c;
                name = string.Join(" ", args[..^1]);
            }
            else
            {
                name = string.Join(" ", args);
            }

            if (!_actions.DropItem(name, count))
                _monitor.Log($"丢弃失败: 找不到 '{name}'", LogLevel.Warn);
        }

        /*────────────────────────────────────────────
         *  agent_ship — 放入出货箱
         *────────────────────────────────────────────*/
        private void CmdShip(string command, string[] args)
        {
            if (!RequireWorld()) return;

            if (args.Length < 1)
            {
                _monitor.Log("用法: agent_ship <name> [count]", LogLevel.Warn);
                return;
            }

            int count = -1;
            string name;
            if (args.Length >= 2 && int.TryParse(args[^1], out int c))
            {
                count = c;
                name = string.Join(" ", args[..^1]);
            }
            else
            {
                name = string.Join(" ", args);
            }

            if (!_actions.ShipItem(name, count))
                _monitor.Log($"出货失败: 找不到 '{name}'", LogLevel.Warn);
        }

        /*────────────────────────────────────────────
         *  agent_tool — 工具状态查询
         *────────────────────────────────────────────*/
        private void CmdToolState(string command, string[] args)
        {
            if (!RequireWorld()) return;

            if (args.Length < 1)
            {
                _monitor.Log("用法: agent_tool <name>", LogLevel.Warn);
                return;
            }

            string name = string.Join(" ", args);
            var ts = _state.GetToolState(name);
            if (ts == null)
            {
                _monitor.Log($"找不到工具: '{name}'", LogLevel.Warn);
                return;
            }

            _monitor.Log($"=== 工具状态 ===", LogLevel.Info);
            _monitor.Log($"  名称: {ts.ToolName} [{ts.UpgradeName}]", LogLevel.Info);
            _monitor.Log($"  槽位: {ts.SlotIndex}", LogLevel.Info);
            if (ts.WaterLevel.HasValue)
                _monitor.Log($"  水量: {ts.WaterLevel}/{ts.WaterCapacity}", LogLevel.Info);
        }

        /*────────────────────────────────────────────
         *  agent_value — 物品价值查询
         *────────────────────────────────────────────*/
        private void CmdItemValue(string command, string[] args)
        {
            if (!RequireWorld()) return;

            if (args.Length < 1)
            {
                _monitor.Log("用法: agent_value <name>", LogLevel.Warn);
                return;
            }

            string name = string.Join(" ", args);
            var v = _state.GetItemValue(name);
            if (v == null)
            {
                _monitor.Log($"背包中找不到: '{name}'", LogLevel.Warn);
                return;
            }

            _monitor.Log($"=== 物品价值 ===", LogLevel.Info);
            _monitor.Log($"  名称: {v.Name} (Q{v.Quality})", LogLevel.Info);
            _monitor.Log($"  单价: {v.SellPrice}g | 数量: x{v.Stack} | 总计: {v.TotalValue}g", LogLevel.Info);
        }

        /*────────────────────────────────────────────
         *  agent_emote — 播放表情
         *────────────────────────────────────────────*/
        private void CmdEmote(string command, string[] args)
        {
            if (!RequireWorld()) return;

            if (args.Length < 1)
            {
                var emotes = string.Join(", ", _actions.GetAvailableEmotes().Keys);
                _monitor.Log($"用法: agent_emote <name|index>  可用: {emotes}", LogLevel.Warn);
                return;
            }

            string emoteName = string.Join(" ", args);
            if (!_actions.DoEmote(emoteName))
            {
                var emotes = string.Join(", ", _actions.GetAvailableEmotes().Keys);
                _monitor.Log($"未知表情 '{emoteName}'，可用: {emotes}", LogLevel.Warn);
            }
        }

        /*────────────────────────────────────────────
         *  agent_cancel — 取消当前动作
         *────────────────────────────────────────────*/
        private void CmdCancel(string command, string[] args)
        {
            _actions.CancelCurrent();
            _monitor.Log("当前动作已取消", LogLevel.Info);
        }

        /*────────────────────────────────────────────
         *  agent — 自然语言指令
         *────────────────────────────────────────────*/
        private void CmdDirective(string command, string[] args)
        {
            if (args.Length < 1)
            {
                _monitor.Log("用法: agent <自然语言指令>  例: agent 去找Abigail聊天", LogLevel.Warn);
                return;
            }

            string directive = string.Join(" ", args);

            if (_emitter == null)
            {
                _monitor.Log("EventEmitter 未初始化，无法发送指令", LogLevel.Error);
                return;
            }

            _emitter.EmitUserDirective(directive, "console");
            _monitor.Log($"[Agent 指令已下达] {directive}", LogLevel.Info);

            // 同时在游戏聊天框显示（如果可用）
            Game1.chatBox?.addMessage($"[指令] {directive}", Color.LightGreen);
        }

        /*────────────────────────────────────────────
         *  agent_feedback — 记录反馈
         *────────────────────────────────────────────*/
        private void CmdFeedback(string command, string[] args)
        {
            if (args.Length < 1)
            {
                _monitor.Log("用法: agent_feedback <text>", LogLevel.Warn);
                return;
            }

            string text = string.Join(" ", args);
            _reporter.AddFeedback(text);
            _monitor.Log($"反馈已记录: {text}", LogLevel.Info);
        }

        /*────────────────────────────────────────────
         *  agent_report — 生成测试报告
         *────────────────────────────────────────────*/
        private void CmdReport(string command, string[] args)
        {
            string path = _reporter.GenerateReport();
            _monitor.Log($"测试报告已生成: {path}", LogLevel.Info);
            _monitor.Log($"  动作记录: {_reporter.ActionCount} 条", LogLevel.Info);
            _monitor.Log($"  错误记录: {_reporter.ErrorCount} 条", LogLevel.Info);
        }

        /*────────────────────────────────────────────
         *  辅助方法
         *────────────────────────────────────────────*/
        private bool RequireWorld()
        {
            if (Context.IsWorldReady) return true;
            _monitor.Log("命令需要加载存档后才能使用", LogLevel.Warn);
            return false;
        }

        private static string FormatTime(int time) => TimeHelper.FormatTime(time);
    }
}
