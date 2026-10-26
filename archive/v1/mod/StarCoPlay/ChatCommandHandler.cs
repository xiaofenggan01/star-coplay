using System;
using System.Linq;
using HarmonyLib;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Menus;
using StarCoPlay.Bridge;

namespace StarCoPlay
{
    /// <summary>
    /// 游戏内聊天命令处理器 — 玩家在聊天框输入 /agent_xxx 触发 Agent 功能。
    /// 通过 Harmony 拦截 ChatBox.runCommand，结果输出到聊天框。
    /// </summary>
    public class ChatCommandHandler
    {
        /// <summary>全局实例（供 Harmony 静态 patch 回调访问）。</summary>
        public static ChatCommandHandler? Instance { get; private set; }

        private readonly IMonitor _monitor;
        private readonly StateReader _state;
        private readonly ActionExecutor _actions;
        private readonly DebugReporter _reporter;

        public ChatCommandHandler(IMonitor monitor, StateReader state, ActionExecutor actions, DebugReporter reporter)
        {
            _monitor = monitor;
            _state = state;
            _actions = actions;
            _reporter = reporter;
            Instance = this;
        }

        /// <summary>处理聊天命令。返回 true 表示已处理。</summary>
        public bool HandleCommand(string command)
        {
            var parts = command.Split(' ', StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length == 0) return false;

            string name = parts[0];
            string[] args = parts.Length > 1 ? parts[1..] : Array.Empty<string>();

            if (!Context.IsWorldReady && name != "agent_help")
            {
                ChatMsg("需要加载存档后才能使用", Color.Orange);
                return true;
            }

            switch (name)
            {
                case "agent_status": CmdStatus(); break;
                case "agent_npcs": CmdNpcs(); break;
                case "agent_objects": CmdObjects(args); break;
                case "agent_farm": CmdFarm(); break;
                case "agent_goto": CmdGoto(args); break;
                case "agent_goto_npc": CmdGotoNpc(args); break;
                case "agent_warp": CmdWarp(args); break;
                case "agent_interact": CmdInteract(args); break;
                case "agent_equip": CmdEquip(args); break;
                case "agent_usetool": CmdUseTool(args); break;
                case "agent_drop": CmdDrop(args); break;
                case "agent_ship": CmdShip(args); break;
                case "agent_tool": CmdToolState(args); break;
                case "agent_value": CmdItemValue(args); break;
                case "agent_emote": CmdEmote(args); break;
                // agent_say (SayBubble) 已移除：SDV 不支持 Farmer 头顶文字
                case "agent_cancel": CmdCancel(); break;
                case "agent_report": CmdReport(); break;
                case "agent_help": CmdHelp(); break;
                case "agent":
                    if (args.Length < 1) { ChatMsg("用法: /agent <自然语言指令>", Color.Orange); break; }
                    string directive = string.Join(" ", args);
                    EventEmitter.Instance?.EmitUserDirective(directive, "chat");
                    ChatMsg($"[指令已下达] {directive}", Color.LightGreen);
                    break;
                default:
                    ChatMsg($"未知命令: /{name}，输入 /agent_help 查看", Color.Orange);
                    break;
            }
            return true;
        }

        /*── 查询命令 ──*/

        private void CmdStatus()
        {
            var world = _state.GetWorldState();
            var player = _state.GetPlayerState();
            ChatMsg($"[Agent] {(string.IsNullOrEmpty(_reporter.CurrentActionName) ? "空闲" : _reporter.CurrentActionName)}");
            ChatMsg($"时间: {world.Season} Y{world.Year} D{world.Day} {TimeHelper.FormatTime(world.Time)} | {world.Weather}");
            ChatMsg($"位置: {player.Location} ({player.TileX},{player.TileY}) | 体力: {player.Energy}/{player.MaxEnergy} | 金币: {player.Gold}");
        }

        private void CmdNpcs()
        {
            var npcs = _state.GetNearbyNpcs(radius: 999);
            if (npcs.Count == 0) { ChatMsg("当前地图无 NPC"); return; }
            ChatMsg($"NPC ({npcs.Count}):");
            foreach (var npc in npcs.Take(5))
                ChatMsg($"  {npc.DisplayName} ({npc.TileX},{npc.TileY}) {npc.Hearts}H");
            if (npcs.Count > 5)
                ChatMsg($"  ...还有 {npcs.Count - 5} 个");
        }

        private void CmdObjects(string[] args)
        {
            int radius = args.Length > 0 && int.TryParse(args[0], out int r) ? r : 10;
            var objects = _state.GetNearbyObjects(radius);
            ChatMsg($"附近物品 (半径{radius}): {objects.Count}");
            foreach (var obj in objects.Take(5))
                ChatMsg($"  ({obj.TileX},{obj.TileY}) {obj.Name} [{obj.Category}]");
            if (objects.Count > 5)
                ChatMsg($"  ...还有 {objects.Count - 5} 个");
        }

        private void CmdFarm()
        {
            var s = _state.GetFarmSummary();
            if (s == null) { ChatMsg("无法获取农场信息", Color.Orange); return; }
            ChatMsg($"农场: {s.CropCount}株作物 | 未浇水:{s.UnwateredCount} | 可收获:{s.HarvestableCount}");
        }

        private void CmdToolState(string[] args)
        {
            if (args.Length < 1) { ChatMsg("用法: /agent_tool <工具名>", Color.Orange); return; }
            string name = string.Join(" ", args);
            var ts = _state.GetToolState(name);
            if (ts == null) { ChatMsg($"找不到工具: {name}", Color.Orange); return; }

            string info = $"{ts.ToolName} [{ts.UpgradeName}] 槽位:{ts.SlotIndex}";
            if (ts.WaterLevel.HasValue)
                info += $" 水量:{ts.WaterLevel}/{ts.WaterCapacity}";
            ChatMsg(info);
        }

        private void CmdItemValue(string[] args)
        {
            if (args.Length < 1) { ChatMsg("用法: /agent_value <物品名>", Color.Orange); return; }
            string name = string.Join(" ", args);
            var v = _state.GetItemValue(name);
            if (v == null) { ChatMsg($"背包中找不到: {name}", Color.Orange); return; }
            ChatMsg($"{v.Name} 售价:{v.SellPrice}g x{v.Stack} = {v.TotalValue}g");
        }

        /*── 动作命令 ──*/

        private void CmdGoto(string[] args)
        {
            if (args.Length < 2 || !int.TryParse(args[0], out int x) || !int.TryParse(args[1], out int y))
            { ChatMsg("用法: /agent_goto <x> <y>", Color.Orange); return; }

            if (_actions.NavigateTo(new Point(x, y)))
                ChatMsg($"导航中 → ({x},{y})");
            else
                ChatMsg($"导航失败: ({x},{y}) 不可达", Color.Orange);
        }

        private void CmdGotoNpc(string[] args)
        {
            if (args.Length < 1) { ChatMsg("用法: /agent_goto_npc <name>", Color.Orange); return; }
            string npcName = string.Join(" ", args);
            var location = Game1.currentLocation;
            if (location == null) return;

            var npc = location.characters.FirstOrDefault(c =>
                c.Name.Equals(npcName, StringComparison.OrdinalIgnoreCase) ||
                c.displayName.Equals(npcName, StringComparison.OrdinalIgnoreCase));

            if (npc == null) { ChatMsg($"当前地图找不到 {npcName}", Color.Orange); return; }

            var target = new Point((int)npc.Tile.X, (int)npc.Tile.Y + 1);
            _actions.NavigateTo(target);
            ChatMsg($"导航中 → {npc.displayName}");
        }

        private void CmdWarp(string[] args)
        {
            if (args.Length < 1) { ChatMsg("用法: /agent_warp <location> [x] [y]", Color.Orange); return; }
            int x = 64, y = 15;
            if (args.Length >= 3) { int.TryParse(args[1], out x); int.TryParse(args[2], out y); }

            if (_actions.WarpTo(args[0], x, y))
                ChatMsg($"传送中 → {args[0]}");
            else
                ChatMsg($"传送失败: {args[0]}", Color.Orange);
        }

        private void CmdInteract(string[] args)
        {
            if (args.Length < 2 || !int.TryParse(args[0], out int x) || !int.TryParse(args[1], out int y))
            { ChatMsg("用法: /agent_interact <x> <y>", Color.Orange); return; }

            bool ok = _actions.InteractWithTile(x, y);
            ChatMsg(ok ? $"交互成功 ({x},{y})" : $"({x},{y}) 无可交互对象", ok ? null : Color.Orange);
        }

        private void CmdEquip(string[] args)
        {
            if (args.Length < 1) { ChatMsg("用法: /agent_equip <name>", Color.Orange); return; }
            string name = string.Join(" ", args);
            if (_actions.EquipItemByName(name))
                ChatMsg($"已装备: {name}");
            else
                ChatMsg($"装备失败: 找不到 {name}", Color.Orange);
        }

        private void CmdUseTool(string[] args)
        {
            if (args.Length < 2 || !int.TryParse(args[0], out int x) || !int.TryParse(args[1], out int y))
            { ChatMsg("用法: /agent_usetool <x> <y>", Color.Orange); return; }

            if (_actions.UseToolAt(x, y))
                ChatMsg($"工具使用 → ({x},{y})");
            else
                ChatMsg("工具使用失败", Color.Orange);
        }

        private void CmdDrop(string[] args)
        {
            if (args.Length < 1) { ChatMsg("用法: /agent_drop <name> [count]", Color.Orange); return; }
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

            if (_actions.DropItem(name, count))
                ChatMsg($"已丢弃: {name}");
            else
                ChatMsg($"丢弃失败: 找不到 {name}", Color.Orange);
        }

        private void CmdShip(string[] args)
        {
            if (args.Length < 1) { ChatMsg("用法: /agent_ship <name> [count]", Color.Orange); return; }
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

            if (_actions.ShipItem(name, count))
                ChatMsg($"已放入出货箱: {name}");
            else
                ChatMsg($"出货失败: 找不到 {name}", Color.Orange);
        }

        /*── 控制命令 ──*/

        private void CmdEmote(string[] args)
        {
            if (args.Length < 1)
            {
                var emotes = string.Join(", ", _actions.GetAvailableEmotes().Keys);
                ChatMsg($"用法: /agent_emote <名称>  可用: {emotes}", Color.Orange);
                return;
            }

            string emoteName = string.Join(" ", args);
            if (_actions.DoEmote(emoteName))
                ChatMsg($"表情: {emoteName}");
            else
            {
                var emotes = string.Join(", ", _actions.GetAvailableEmotes().Keys);
                ChatMsg($"未知表情: {emoteName}  可用: {emotes}", Color.Orange);
            }
        }

        private void CmdCancel()
        {
            _actions.CancelCurrent();
            ChatMsg("动作已取消");
        }

        private void CmdReport()
        {
            string path = _reporter.GenerateReport();
            ChatMsg($"报告已生成 ({_reporter.ActionCount}条动作, {_reporter.ErrorCount}条错误)");
        }

        private void CmdHelp()
        {
            ChatMsg("[Agent 命令列表]", Color.LightGreen);
            ChatMsg("/agent <指令> — 自然语言指令");
            ChatMsg("/agent_status — 状态概览");
            ChatMsg("/agent_goto X Y — 寻路");
            ChatMsg("/agent_warp LOC — 传送");
            ChatMsg("/agent_equip NAME — 装备");
            ChatMsg("/agent_usetool X Y — 使用工具");
            ChatMsg("/agent_drop NAME [N] — 丢弃");
            ChatMsg("/agent_ship NAME [N] — 出货");
            ChatMsg("/agent_tool NAME — 工具状态");
            ChatMsg("/agent_value NAME — 物品价值");
            ChatMsg("/agent_npcs — NPC列表");
            ChatMsg("/agent_objects [R] — 附近物品");
            ChatMsg("/agent_farm — 农场概况");
            ChatMsg("/agent_emote NAME — 表情");
            ChatMsg("/agent_cancel — 取消动作");
        }

        /*── 输出辅助 ──*/

        private static void ChatMsg(string msg, Color? color = null)
        {
            Game1.chatBox?.addMessage(msg, color ?? Color.White);
        }
    }

    /*────────────────────────────────────────────
     *  Harmony Patch: 拦截 ChatBox.runCommand
     *  SDV 聊天框输入 /xxx 时，去掉 / 后调用 runCommand("xxx")。
     *  我们在此拦截 agent_ 前缀的命令。
     *────────────────────────────────────────────*/

    [HarmonyPatch(typeof(ChatBox), "runCommand")]
    public static class ChatBoxRunCommandPatch
    {
        public static bool Prefix(string commandText)
        {
            if (ChatCommandHandler.Instance == null) return true;
            if (!commandText.StartsWith("agent_")) return true;

            // 处理命令并阻止原始方法执行
            return !ChatCommandHandler.Instance.HandleCommand(commandText);
        }
    }

    /// <summary>
    /// Harmony Patch: 拦截 ChatBox.receiveChatMessage
    /// 多人模式下其他玩家的聊天消息会经过此方法，转发到 events.jsonl 供 Agent 感知。
    /// </summary>
    [HarmonyPatch(typeof(ChatBox), "receiveChatMessage")]
    public static class ChatBoxReceiveChatPatch
    {
        public static void Postfix(long sourceFarmer, int chatKind, LocalizedContentManager.LanguageCode language, string message)
        {
            // chatKind 0 = 普通聊天消息，只拦截普通聊天
            if (chatKind != 0) return;
            if (EventEmitter.Instance == null) return;

            // 获取发送者名称
            string playerName = "Unknown";
            if (Game1.getAllFarmers() != null)
            {
                foreach (var farmer in Game1.getAllFarmers())
                {
                    if (farmer.UniqueMultiplayerID == sourceFarmer)
                    {
                        playerName = farmer.Name;
                        break;
                    }
                }
            }

            EventEmitter.Instance.EmitChatReceived(sourceFarmer, playerName, message);
        }
    }
}
