using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Menus;
using StardewValley.Objects;
using StardewValley.Pathfinding;

namespace StarCoPlay
{
    /// <summary>
    /// 动作执行层 — Agent 的"手脚"。
    /// 管理寻路移动、物品交互等游戏动作的执行。
    /// </summary>
    public class ActionExecutor
    {
        private readonly IMonitor _monitor;

        /*── 调试报告器（可选注入） ──*/
        private DebugReporter? _reporter;

        /*── 状态机 ──*/
        private ActionState _currentState = ActionState.Idle;
        private PathFindController? _pathController;
        private Point _navigationTarget;
        private int _navigationTimeoutTicks;
        private const int MaxNavigationTicks = 600; // 10 秒超时

        /*── 计时 ──*/
        private Stopwatch? _actionTimer;

        /*── 导航动画 ──*/
        private int _lastNavDirection = -1;

        public ActionExecutor(IMonitor monitor)
        {
            _monitor = monitor;
        }

        /// <summary>注入 DebugReporter（ModEntry 初始化后调用）。</summary>
        public void SetReporter(DebugReporter reporter)
        {
            _reporter = reporter;
        }

        /// <summary>当前是否有动作正在执行。</summary>
        public bool IsBusy => _currentState != ActionState.Idle;

        /// <summary>当前动作状态（供 HUD 显示）。</summary>
        public ActionState CurrentState => _currentState;

        /// <summary>当前导航目标（供 HUD 显示）。</summary>
        public Point NavigationTarget => _navigationTarget;

        /*────────────────────────────────────────────
         *  Tick — 每帧驱动，由 ModEntry.OnUpdateTicked 调用
         *────────────────────────────────────────────*/
        public void Tick()
        {
            switch (_currentState)
            {
                case ActionState.Navigating:
                    TickNavigation();
                    break;

                case ActionState.Interacting:
                    // 交互通常是瞬时的，在发起时立即完成
                    break;

                case ActionState.Idle:
                default:
                    break;
            }
        }

        /*────────────────────────────────────────────
         *  NavigateTo — 使用 PathFindController 寻路移动
         *────────────────────────────────────────────*/

        /// <summary>
        /// 导航到目标瓦片（同地图内）。
        /// 使用游戏内置 PathFindController（A* 寻路）。
        /// </summary>
        public bool NavigateTo(Point target)
        {
            if (!Context.CanPlayerMove)
            {
                _monitor.Log($"NavigateTo 失败: 玩家当前无法移动 (CanPlayerMove=false, UsingTool={Game1.player.UsingTool}, canMove={Game1.player.canMove})", LogLevel.Warn);
                _reporter?.LogError("NavigateTo", $"玩家无法移动: UsingTool={Game1.player.UsingTool}, canMove={Game1.player.canMove}");
                return false;
            }

            var location = Game1.currentLocation;
            if (location == null)
            {
                _monitor.Log("NavigateTo 失败: 当前无地图 (currentLocation=null)", LogLevel.Warn);
                _reporter?.LogError("NavigateTo", "当前无地图");
                return false;
            }

            // 记录动作开始
            _reporter?.LogActionStart("NavigateTo", new Dictionary<string, object>
            {
                ["x"] = target.X,
                ["y"] = target.Y
            });
            _actionTimer = Stopwatch.StartNew();

            // 创建寻路控制器
            _pathController = new PathFindController(
                Game1.player,       // 角色
                location,           // 当前地图
                target,             // 目标 Point
                -1,                 // 方向（-1=自动）
                OnPathComplete      // 完成回调
            );

            // 检查是否找到有效路径
            if (_pathController.pathToEndPoint == null || _pathController.pathToEndPoint.Count == 0)
            {
                _monitor.Log($"NavigateTo 失败: 无法找到到 ({target.X},{target.Y}) 的路径", LogLevel.Warn);
                _reporter?.LogActionEnd("fail_no_path", _actionTimer?.ElapsedMilliseconds ?? 0);
                _reporter?.LogError("NavigateTo", $"无法找到到 ({target.X},{target.Y}) 的路径");
                _pathController = null;
                _actionTimer = null;
                return false;
            }

            // 将控制器挂载到玩家身上
            Game1.player.controller = _pathController;

            _navigationTarget = target;
            _navigationTimeoutTicks = 0;
            _lastNavDirection = -1;
            _currentState = ActionState.Navigating;

            _monitor.Log($"NavigateTo 开始: 前往 ({target.X},{target.Y})，路径长度 {_pathController.pathToEndPoint.Count} 步", LogLevel.Debug);
            return true;
        }

        /// <summary>
        /// 跨地图传送。
        /// 使用 Game1.warpFarmer 进行地图间移动。
        /// </summary>
        public bool WarpTo(string locationName, int tileX, int tileY, int facingDirection = 2)
        {
            if (!Context.IsPlayerFree)
            {
                _monitor.Log("WarpTo 失败: 玩家当前不空闲", LogLevel.Warn);
                _reporter?.LogError("WarpTo", "玩家当前不空闲");
                return false;
            }

            var targetLocation = Utility.fuzzyLocationSearch(locationName);
            if (targetLocation == null)
            {
                _monitor.Log($"WarpTo 失败: 找不到地图 '{locationName}'", LogLevel.Warn);
                _reporter?.LogError("WarpTo", $"找不到地图 '{locationName}'");
                return false;
            }

            // 记录动作开始
            _reporter?.LogActionStart("WarpTo", new Dictionary<string, object>
            {
                ["location"] = locationName,
                ["x"] = tileX,
                ["y"] = tileY
            });
            var timer = Stopwatch.StartNew();

            // 立即记录动作完成（warp 是瞬时发起的，延迟仅用于游戏线程安全）
            _reporter?.LogActionEnd("success", 0);

            // 必须延迟执行 warp（游戏线程安全）
            DelayedAction.functionAfterDelay(() =>
            {
                Game1.warpFarmer(locationName, tileX, tileY, facingDirection);
                _monitor.Log($"WarpTo 完成: 传送到 {locationName} ({tileX},{tileY})", LogLevel.Debug);
            }, 100);

            return true;
        }

        /*────────────────────────────────────────────
         *  InteractWithTile — 与指定瓦片上的对象交互
         *────────────────────────────────────────────*/

        /// <summary>
        /// 与目标瓦片上的对象交互 — 调用 GameLocation.checkAction，
        /// 等同于玩家在该瓦片按下交互键（X），游戏内置逻辑统一处理
        /// forage 拾取、箱子、机器、NPC、家具等所有可交互对象。
        /// </summary>
        public bool InteractWithTile(int tileX, int tileY)
        {
            var location = Game1.currentLocation;
            if (location == null)
            {
                _monitor.Log("InteractWithTile 失败: currentLocation=null", LogLevel.Warn);
                return false;
            }

            _reporter?.LogActionStart("InteractWithTile", new Dictionary<string, object>
            {
                ["x"] = tileX,
                ["y"] = tileY,
                ["location"] = location.Name
            });
            var timer = Stopwatch.StartNew();

            // 面朝目标瓦片（交互前需要正确朝向）
            int direction = GetDirectionTo(tileX, tileY);
            Game1.player.faceDirection(direction);

            // 调用 GameLocation.checkAction — 游戏按 X 键的内部入口
            // 统一处理所有交互类型：forage、chest、machine、NPC、furniture 等
            var tileLoc = new xTile.Dimensions.Location(tileX, tileY);
            bool result = location.checkAction(tileLoc, Game1.viewport, Game1.player);

            timer.Stop();
            _monitor.Log($"Interact: checkAction @ ({tileX},{tileY}) → {(result ? "成功" : "无响应")}", LogLevel.Debug);
            _reporter?.LogActionEnd(result ? "success" : "fail_no_response", timer.ElapsedMilliseconds);
            return result;
        }

        /*────────────────────────────────────────────
         *  EquipItem — 切换当前装备
         *────────────────────────────────────────────*/

        /// <summary>
        /// 装备指定槽位的物品（仅快捷栏 0-11）。
        /// </summary>
        public bool EquipItem(int slotIndex)
        {
            if (slotIndex < 0 || slotIndex >= 12)
            {
                _monitor.Log($"EquipItem 失败: 槽位 {slotIndex} 不在快捷栏范围 (0-11)", LogLevel.Warn);
                return false;
            }

            var item = Game1.player.Items[slotIndex];
            if (item == null)
            {
                _monitor.Log($"EquipItem 失败: 槽位 {slotIndex} 为空", LogLevel.Warn);
                return false;
            }

            Game1.player.CurrentToolIndex = slotIndex;
            _monitor.Log($"EquipItem: 切换到槽位 {slotIndex} ({item.DisplayName})", LogLevel.Debug);
            _reporter?.LogActionStart("EquipItem", new Dictionary<string, object>
            {
                ["slot"] = slotIndex,
                ["item"] = item.DisplayName
            });
            _reporter?.LogActionEnd("success", 0);
            return true;
        }

        /// <summary>
        /// 按名称装备物品（在快捷栏中查找）。
        /// </summary>
        public bool EquipItemByName(string itemName)
        {
            for (int i = 0; i < 12 && i < Game1.player.Items.Count; i++)
            {
                var item = Game1.player.Items[i];
                if (item != null &&
                    (item.DisplayName.Equals(itemName, System.StringComparison.OrdinalIgnoreCase) ||
                     item.Name.Equals(itemName, System.StringComparison.OrdinalIgnoreCase)))
                    return EquipItem(i);
            }

            _monitor.Log($"EquipItemByName 失败: 快捷栏中找不到 '{itemName}'", LogLevel.Warn);
            _reporter?.LogError("EquipItemByName", $"快捷栏中找不到 '{itemName}'");
            return false;
        }

        /*────────────────────────────────────────────
         *  SendChatMessage — 游戏内聊天
         *────────────────────────────────────────────*/

        /// <summary>在游戏聊天框发送消息（通过游戏原生聊天系统，多人模式下所有玩家可见）。</summary>
        public void SendChat(string message)
        {
            if (Game1.chatBox == null)
            {
                _monitor.Log("ChatBox 不可用，无法发送消息", LogLevel.Warn);
                return;
            }

            // textBoxEnter(string) 内部调用 Game1.multiplayer.sendChatMessage()
            // 走游戏原生多人聊天协议，广播给所有连接的玩家
            Game1.chatBox.textBoxEnter(message);

            _monitor.Log($"Chat: {message}", LogLevel.Debug);
            _reporter?.LogActionStart("SendChat", new Dictionary<string, object> { ["message"] = message });
            _reporter?.LogActionEnd("success", 0);
        }

        /*────────────────────────────────────────────
         *  ShowHUDMessage — 左下角 HUD 通知
         *────────────────────────────────────────────*/

        /// <summary>在游戏左下角显示 HUD 通知消息。</summary>
        public void ShowHUDMessage(string message, int type = 0)
        {
            // HUDMessage type: 0=普通(默认), 1=错误(红色), 2=成就/星星, 3=新配方, 4=新任务
            Game1.addHUDMessage(new HUDMessage(message, type));
            _monitor.Log($"HUDMessage: [{type}] {message}", LogLevel.Debug);
            _reporter?.LogActionStart("ShowHUDMessage", new Dictionary<string, object>
            {
                ["message"] = message,
                ["type"] = type
            });
            _reporter?.LogActionEnd("success", 0);
        }

        /*────────────────────────────────────────────
         *  DoEmote — 玩家表情动画
         *────────────────────────────────────────────*/

        /// <summary>已知表情名称到 emoteIndex 的映射。</summary>
        private static readonly Dictionary<string, int> EmoteMap = new(StringComparer.OrdinalIgnoreCase)
        {
            ["happy"]       = 32,
            ["sad"]         = 28,
            ["heart"]       = 20,
            ["love"]        = 20,
            ["exclamation"] = 16,
            ["alert"]       = 16,
            ["angry"]       = 12,
            ["question"]    = 8,
            ["sleep"]       = 24,
            ["zzz"]         = 24,
            ["sick"]        = 36,
            ["laugh"]       = 40,
            ["haha"]        = 40,
            ["note"]        = 56,  // 音符
            ["music"]       = 56,
            ["blush"]       = 60,
            ["thinking"]    = 8,   // 同 question
        };

        /// <summary>
        /// 让玩家角色播放表情气泡动画。
        /// 支持按名称（如 "happy"、"heart"）或直接传 emoteIndex。
        /// </summary>
        public bool DoEmote(string emoteName)
        {
            if (EmoteMap.TryGetValue(emoteName, out int index))
            {
                Game1.player.doEmote(index);
                _monitor.Log($"DoEmote: {emoteName} (index={index})", LogLevel.Debug);
                _reporter?.LogActionStart("DoEmote", new Dictionary<string, object>
                {
                    ["emote"] = emoteName,
                    ["index"] = index
                });
                _reporter?.LogActionEnd("success", 0);
                return true;
            }

            // 尝试直接解析为数字
            if (int.TryParse(emoteName, out int directIndex) && directIndex >= 0)
            {
                Game1.player.doEmote(directIndex);
                _monitor.Log($"DoEmote: index={directIndex}", LogLevel.Debug);
                _reporter?.LogActionStart("DoEmote", new Dictionary<string, object>
                {
                    ["emote"] = emoteName,
                    ["index"] = directIndex
                });
                _reporter?.LogActionEnd("success", 0);
                return true;
            }

            var available = string.Join(", ", EmoteMap.Keys);
            _monitor.Log($"DoEmote 失败: 未知表情 '{emoteName}'，可用: {available}", LogLevel.Warn);
            _reporter?.LogError("DoEmote", $"未知表情 '{emoteName}'，可用: {available}");
            return false;
        }

        /// <summary>获取所有可用表情名称列表。</summary>
        public IReadOnlyDictionary<string, int> GetAvailableEmotes() => EmoteMap;

        /*────────────────────────────────────────────
         *  UseToolAt — 对指定瓦片使用当前装备的工具
         *────────────────────────────────────────────*/

        /// <summary>
        /// 对目标瓦片使用当前工具（挖矿/浇水/砍树/锄地等）。
        /// 玩家需先装备对应工具，且目标应在相邻范围内。
        /// </summary>
        public bool UseToolAt(int tileX, int tileY)
        {
            var player = Game1.player;
            var tool = player.CurrentTool;
            if (tool == null)
            {
                _monitor.Log($"UseToolAt 失败: 未装备工具 (CurrentItem={player.CurrentItem?.DisplayName ?? "null"})", LogLevel.Warn);
                _reporter?.LogError("UseToolAt", $"未装备工具, CurrentItem={player.CurrentItem?.DisplayName ?? "null"}");
                return false;
            }

            var location = Game1.currentLocation;
            if (location == null)
            {
                _monitor.Log("UseToolAt 失败: currentLocation=null", LogLevel.Warn);
                return false;
            }

            // 朝向目标
            int direction = GetDirectionTo(tileX, tileY);
            player.faceDirection(direction);

            _reporter?.LogActionStart("UseToolAt", new Dictionary<string, object>
            {
                ["tool"] = tool.DisplayName,
                ["toolType"] = tool.GetType().Name,
                ["x"] = tileX,
                ["y"] = tileY,
                ["direction"] = direction
            });
            var timer = Stopwatch.StartNew();

            try
            {
                // DoFunction 接受像素坐标（瓦片 × 64）
                tool.DoFunction(location, tileX * 64, tileY * 64, 1, player);
                timer.Stop();
                _monitor.Log($"UseToolAt: {tool.DisplayName} @ ({tileX},{tileY}) dir={direction} ({timer.ElapsedMilliseconds}ms)", LogLevel.Debug);
                _reporter?.LogActionEnd("success", timer.ElapsedMilliseconds);
                return true;
            }
            catch (Exception ex)
            {
                timer.Stop();
                _monitor.Log($"UseToolAt 异常: {tool.DisplayName} @ ({tileX},{tileY}): {ex.Message}", LogLevel.Error);
                _reporter?.LogActionEnd("error", timer.ElapsedMilliseconds);
                _reporter?.LogError("UseToolAt", $"{tool.DisplayName} @ ({tileX},{tileY}): {ex.Message}", ex.StackTrace);
                return false;
            }
        }

        /*────────────────────────────────────────────
         *  DropItem — 丢弃物品到地面
         *────────────────────────────────────────────*/

        /// <summary>按名称丢弃背包中的物品。</summary>
        public bool DropItem(string itemName, int count = -1)
        {
            var player = Game1.player;
            var location = Game1.currentLocation;
            if (location == null) return false;

            for (int i = 0; i < player.Items.Count; i++)
            {
                var item = player.Items[i];
                if (item == null) continue;
                if (!item.DisplayName.Equals(itemName, StringComparison.OrdinalIgnoreCase) &&
                    !item.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase))
                    continue;

                _reporter?.LogActionStart("DropItem", new Dictionary<string, object>
                {
                    ["item"] = item.DisplayName,
                    ["count"] = count > 0 ? count : item.Stack
                });

                if (count > 0 && count < item.Stack)
                {
                    var dropped = item.getOne();
                    dropped.Stack = count;
                    item.Stack -= count;
                    Game1.createItemDebris(dropped, player.Position, player.FacingDirection, location);
                }
                else
                {
                    Game1.createItemDebris(item, player.Position, player.FacingDirection, location);
                    player.Items[i] = null;
                }

                _monitor.Log($"DropItem: {itemName} x{(count > 0 ? count : item.Stack)}", LogLevel.Debug);
                _reporter?.LogActionEnd("success", 0);
                return true;
            }

            _monitor.Log($"DropItem 失败: 背包中找不到 '{itemName}'", LogLevel.Warn);
            _reporter?.LogError("DropItem", $"背包中找不到 '{itemName}'");
            return false;
        }

        /*────────────────────────────────────────────
         *  ShipItem — 放入出货箱（次日结算售出）
         *────────────────────────────────────────────*/

        /// <summary>将背包中的物品放入出货箱。</summary>
        public bool ShipItem(string itemName, int count = -1)
        {
            var player = Game1.player;
            var farm = Game1.getFarm();
            if (farm == null)
            {
                _monitor.Log("ShipItem 失败: 无法访问农场", LogLevel.Warn);
                return false;
            }

            for (int i = 0; i < player.Items.Count; i++)
            {
                var item = player.Items[i];
                if (item == null) continue;
                if (!item.DisplayName.Equals(itemName, StringComparison.OrdinalIgnoreCase) &&
                    !item.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase))
                    continue;

                _reporter?.LogActionStart("ShipItem", new Dictionary<string, object>
                {
                    ["item"] = item.DisplayName,
                    ["count"] = count > 0 ? count : item.Stack,
                    ["unitPrice"] = item.sellToStorePrice()
                });

                Item toShip;
                if (count > 0 && count < item.Stack)
                {
                    toShip = item.getOne();
                    toShip.Stack = count;
                    item.Stack -= count;
                }
                else
                {
                    toShip = item;
                    player.Items[i] = null;
                }

                farm.getShippingBin(player).Add(toShip);

                int total = toShip.sellToStorePrice() * toShip.Stack;
                _monitor.Log($"ShipItem: {toShip.DisplayName} x{toShip.Stack} (预计 {total}g)", LogLevel.Debug);
                _reporter?.LogActionEnd("success", 0);
                return true;
            }

            _monitor.Log($"ShipItem 失败: 背包中找不到 '{itemName}'", LogLevel.Warn);
            _reporter?.LogError("ShipItem", $"背包中找不到 '{itemName}'");
            return false;
        }

        /*────────────────────────────────────────────
         *  GetActiveDialog — 读取当前对话框信息
         *────────────────────────────────────────────*/

        /// <summary>
        /// 获取当前活跃的对话框信息。
        /// 检查 Game1.activeClickableMenu 是否为 DialogueBox，
        /// 返回对话文本和可选的回答选项。
        /// </summary>
        public Dictionary<string, object>? GetActiveDialog()
        {
            var menu = Game1.activeClickableMenu;

            // 检查是否有 DialogueBox
            if (menu is DialogueBox dialogueBox)
            {
                var result = new Dictionary<string, object>
                {
                    ["is_active"] = true
                };

                // 获取对话文本
                // DialogueBox.getCurrentString() 返回当前显示的文本
                try
                {
                    string currentText = dialogueBox.getCurrentString();
                    result["text"] = currentText ?? "";
                }
                catch
                {
                    result["text"] = "";
                }

                // 检查是否有回答选项（问题对话框）
                if (dialogueBox.isQuestion)
                {
                    result["dialog_type"] = "question";
                    var choices = new List<Dictionary<string, object>>();

                    if (dialogueBox.responses != null)
                    {
                        for (int i = 0; i < dialogueBox.responses.Length; i++)
                        {
                            var response = dialogueBox.responses[i];
                            choices.Add(new Dictionary<string, object>
                            {
                                ["index"] = i,
                                ["text"] = response.responseText ?? "",
                                ["key"] = response.responseKey ?? ""
                            });
                        }
                    }

                    result["choices"] = choices;
                }
                else
                {
                    result["dialog_type"] = "info";
                    result["choices"] = new List<Dictionary<string, object>>();
                }

                _monitor.Log($"GetActiveDialog: type={result["dialog_type"]}, text={result["text"]}", LogLevel.Debug);
                return result;
            }

            // 没有活跃对话框
            _monitor.Log("GetActiveDialog: 无活跃对话框", LogLevel.Debug);
            return null;
        }

        /*────────────────────────────────────────────
         *  AnswerDialog — 回答/关闭当前对话框
         *────────────────────────────────────────────*/

        /// <summary>
        /// 回答或关闭当前对话框。
        /// 如果是问题对话框 → 选择指定索引的回答。
        /// 如果是信息对话框 → 关闭/推进对话。
        /// </summary>
        public (bool success, string message, string? errorCode) AnswerDialog(int choiceIndex)
        {
            var menu = Game1.activeClickableMenu;

            if (menu is not DialogueBox dialogueBox)
            {
                return (false, "当前没有活跃的对话框", "NO_DIALOG");
            }

            // 问题对话框 → 选择回答
            if (dialogueBox.isQuestion)
            {
                if (dialogueBox.responses == null || dialogueBox.responses.Length == 0)
                {
                    return (false, "对话框无可用选项", "DIALOG_ERROR");
                }

                if (choiceIndex < 0 || choiceIndex >= dialogueBox.responses.Length)
                {
                    return (false,
                        $"选项索引 {choiceIndex} 超出范围 (0-{dialogueBox.responses.Length - 1})",
                        "INVALID_CHOICE");
                }

                // 设置选中的回答索引并触发选择
                dialogueBox.selectedResponse = choiceIndex;
                dialogueBox.receiveLeftClick(0, 0);

                string choiceText = dialogueBox.responses.Length > choiceIndex
                    ? dialogueBox.responses[choiceIndex].responseText ?? ""
                    : "";
                _monitor.Log($"AnswerDialog: 选择了第 {choiceIndex} 项: {choiceText}", LogLevel.Debug);
                _reporter?.LogActionStart("AnswerDialog", new Dictionary<string, object>
                {
                    ["choiceIndex"] = choiceIndex,
                    ["choiceText"] = choiceText
                });
                _reporter?.LogActionEnd("success", 0);
                return (true, $"已选择: {choiceText}", null);
            }

            // 信息对话框 → 点击推进/关闭
            dialogueBox.receiveLeftClick(0, 0);
            _monitor.Log("AnswerDialog: 推进/关闭信息对话框", LogLevel.Debug);
            _reporter?.LogActionStart("AnswerDialog", new Dictionary<string, object>
            {
                ["action"] = "dismiss"
            });
            _reporter?.LogActionEnd("success", 0);
            return (true, "已推进/关闭对话框", null);
        }

        /*────────────────────────────────────────────
         *  PeekChest — 查看箱子内容（不打开 UI）
         *────────────────────────────────────────────*/

        /// <summary>
        /// 查看指定瓦片上箱子的物品列表，不打开 UI。
        /// 返回物品列表（name, stack, quality），箱子不存在返回 null。
        /// </summary>
        public List<Dictionary<string, object>>? PeekChest(int tileX, int tileY)
        {
            var location = Game1.currentLocation;
            if (location == null)
            {
                _monitor.Log("PeekChest 失败: currentLocation=null", LogLevel.Warn);
                return null;
            }

            var tileKey = new Vector2(tileX, tileY);
            if (!location.objects.TryGetValue(tileKey, out var obj) || obj is not Chest chest)
            {
                _monitor.Log($"PeekChest 失败: ({tileX},{tileY}) 处无箱子", LogLevel.Warn);
                return null;
            }

            var items = new List<Dictionary<string, object>>();
            foreach (var item in chest.Items)
            {
                if (item == null) continue;
                items.Add(new Dictionary<string, object>
                {
                    ["name"] = item.DisplayName,
                    ["stack"] = item.Stack,
                    ["quality"] = item is StardewValley.Object o ? o.Quality : 0
                });
            }

            _monitor.Log($"PeekChest: ({tileX},{tileY}) 含 {items.Count} 种物品", LogLevel.Debug);
            _reporter?.LogActionStart("PeekChest", new Dictionary<string, object>
            {
                ["x"] = tileX, ["y"] = tileY, ["itemCount"] = items.Count
            });
            _reporter?.LogActionEnd("success", 0);
            return items;
        }

        /*────────────────────────────────────────────
         *  StoreInChest — 从背包存入箱子
         *────────────────────────────────────────────*/

        /// <summary>
        /// 将玩家背包中的物品转移到指定瓦片的箱子中。
        /// count=-1 表示全部。返回 (success, errorCode)。
        /// </summary>
        public (bool success, string? errorCode) StoreInChest(int tileX, int tileY, string itemName, int count)
        {
            var location = Game1.currentLocation;
            if (location == null) return (false, "NO_CHEST");

            var tileKey = new Vector2(tileX, tileY);
            if (!location.objects.TryGetValue(tileKey, out var obj) || obj is not Chest chest)
            {
                _monitor.Log($"StoreInChest 失败: ({tileX},{tileY}) 处无箱子", LogLevel.Warn);
                return (false, "NO_CHEST");
            }

            var player = Game1.player;
            int slotIndex = -1;
            for (int i = 0; i < player.Items.Count; i++)
            {
                var item = player.Items[i];
                if (item != null &&
                    (item.DisplayName.Equals(itemName, StringComparison.OrdinalIgnoreCase) ||
                     item.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase)))
                {
                    slotIndex = i;
                    break;
                }
            }

            if (slotIndex < 0)
            {
                _monitor.Log($"StoreInChest 失败: 背包中找不到 '{itemName}'", LogLevel.Warn);
                return (false, "ITEM_NOT_FOUND");
            }

            var sourceItem = player.Items[slotIndex];
            int transferCount = (count <= 0 || count >= sourceItem!.Stack) ? sourceItem.Stack : count;

            _reporter?.LogActionStart("StoreInChest", new Dictionary<string, object>
            {
                ["x"] = tileX, ["y"] = tileY,
                ["item"] = sourceItem.DisplayName, ["count"] = transferCount
            });

            Item toStore;
            if (transferCount >= sourceItem.Stack)
            {
                toStore = sourceItem;
                player.Items[slotIndex] = null;
            }
            else
            {
                toStore = sourceItem.getOne();
                toStore.Stack = transferCount;
                sourceItem.Stack -= transferCount;
            }

            var leftover = chest.addItem(toStore);
            if (leftover != null)
            {
                if (player.Items[slotIndex] == null)
                    player.Items[slotIndex] = leftover;
                else
                    player.Items[slotIndex]!.Stack += leftover.Stack;

                _monitor.Log($"StoreInChest 失败: 箱子已满", LogLevel.Warn);
                _reporter?.LogActionEnd("fail_chest_full", 0);
                return (false, "CHEST_FULL");
            }

            _monitor.Log($"StoreInChest: {sourceItem.DisplayName} x{transferCount} → ({tileX},{tileY})", LogLevel.Debug);
            _reporter?.LogActionEnd("success", 0);
            return (true, null);
        }

        /*────────────────────────────────────────────
         *  TakeFromChest — 从箱子取出到背包
         *────────────────────────────────────────────*/

        /// <summary>
        /// 从指定瓦片的箱子中取出物品到玩家背包。
        /// count=-1 表示全部。返回 (success, errorCode)。
        /// </summary>
        public (bool success, string? errorCode) TakeFromChest(int tileX, int tileY, string itemName, int count)
        {
            var location = Game1.currentLocation;
            if (location == null) return (false, "NO_CHEST");

            var tileKey = new Vector2(tileX, tileY);
            if (!location.objects.TryGetValue(tileKey, out var obj) || obj is not Chest chest)
            {
                _monitor.Log($"TakeFromChest 失败: ({tileX},{tileY}) 处无箱子", LogLevel.Warn);
                return (false, "NO_CHEST");
            }

            int chestSlot = -1;
            for (int i = 0; i < chest.Items.Count; i++)
            {
                var item = chest.Items[i];
                if (item != null &&
                    (item.DisplayName.Equals(itemName, StringComparison.OrdinalIgnoreCase) ||
                     item.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase)))
                {
                    chestSlot = i;
                    break;
                }
            }

            if (chestSlot < 0)
            {
                _monitor.Log($"TakeFromChest 失败: 箱子中找不到 '{itemName}'", LogLevel.Warn);
                return (false, "ITEM_NOT_FOUND");
            }

            var chestItem = chest.Items[chestSlot];
            int transferCount = (count <= 0 || count >= chestItem!.Stack) ? chestItem.Stack : count;

            _reporter?.LogActionStart("TakeFromChest", new Dictionary<string, object>
            {
                ["x"] = tileX, ["y"] = tileY,
                ["item"] = chestItem.DisplayName, ["count"] = transferCount
            });

            Item toTake;
            if (transferCount >= chestItem.Stack)
            {
                toTake = chestItem;
                chest.Items[chestSlot] = null;
            }
            else
            {
                toTake = chestItem.getOne();
                toTake.Stack = transferCount;
                chestItem.Stack -= transferCount;
            }

            bool added = Game1.player.addItemToInventoryBool(toTake);
            if (!added)
            {
                if (chest.Items[chestSlot] == null)
                    chest.Items[chestSlot] = toTake;
                else
                    chest.Items[chestSlot]!.Stack += toTake.Stack;

                _monitor.Log($"TakeFromChest 失败: 背包已满", LogLevel.Warn);
                _reporter?.LogActionEnd("fail_inventory_full", 0);
                return (false, "INVENTORY_FULL");
            }

            _monitor.Log($"TakeFromChest: ({tileX},{tileY}) → {chestItem.DisplayName} x{transferCount}", LogLevel.Debug);
            _reporter?.LogActionEnd("success", 0);
            return (true, null);
        }

        /*────────────────────────────────────────────
         *  CancelCurrent — 取消当前动作
         *────────────────────────────────────────────*/
        public void CancelCurrent()
        {
            var prevState = _currentState;

            if (_currentState == ActionState.Navigating)
            {
                Game1.player.controller = null;
                _pathController = null;
                Game1.player.Halt();

                _actionTimer?.Stop();
                _reporter?.LogActionEnd("cancelled", _actionTimer?.ElapsedMilliseconds ?? 0);
                _actionTimer = null;
            }

            _currentState = ActionState.Idle;
            _monitor.Log($"动作已取消 (prevState={prevState}, UsingTool={Game1.player.UsingTool}, canMove={Game1.player.canMove})", LogLevel.Debug);
        }

        /*────────────────────────────────────────────
         *  内部方法
         *────────────────────────────────────────────*/
        private void TickNavigation()
        {
            _navigationTimeoutTicks++;

            // 超时检测
            if (_navigationTimeoutTicks >= MaxNavigationTicks)
            {
                _monitor.Log($"NavigateTo 超时: 10 秒内未到达 ({_navigationTarget.X},{_navigationTarget.Y})", LogLevel.Warn);
                _reporter?.LogError("NavigateTo", $"超时: 10 秒内未到达 ({_navigationTarget.X},{_navigationTarget.Y})");
                CancelCurrent();
                return;
            }

            var player = Game1.player;

            // 检查是否到达（控制器被游戏清除 = 到达）
            if (player.controller == null)
            {
                player.Halt(); // 停止行走动画
                _lastNavDirection = -1;

                var playerTile = player.Tile;
                _monitor.Log($"NavigateTo 到达: ({(int)playerTile.X},{(int)playerTile.Y})", LogLevel.Debug);

                _actionTimer?.Stop();
                _reporter?.LogActionEnd("success", _actionTimer?.ElapsedMilliseconds ?? 0);
                _actionTimer = null;

                _pathController = null;
                _currentState = ActionState.Idle;
                return;
            }

            // 驱动行走动画
            // PathFindController 更新 Farmer 的位置和朝向，但不驱动行走精灵帧。
            // SDV 的 Farmer.Update 只在 isMoving() 为 true 时递进精灵帧动画，
            // 而 isMoving() 依赖 movementDirections 列表（由 setMoving 填充）。
            // 游戏每帧末尾会清除移动标志，所以必须每帧重新设置。
            //
            // 关键：
            // 1. 每帧都调用 setMoving 保持移动标志（否则动画不播放）
            // 2. 方向变化时先 Halt() 清除旧方向标志（避免标志位累加导致斜向移动）
            // 3. 方向不变时直接 setMoving，不调用 Halt（避免动画帧重置导致顿挫）
            // 4. 设置 running=true 让移动速度匹配 PathFindController 的预期
            int currentDir = player.FacingDirection;

            if (currentDir != _lastNavDirection)
            {
                // 方向变化：先清除旧移动状态，再设置新方向
                player.Halt();
                _lastNavDirection = currentDir;
            }

            byte dirFlag = currentDir switch
            {
                0 => 1,  // up
                1 => 2,  // right
                2 => 4,  // down
                3 => 8,  // left
                _ => 0
            };
            player.setMoving(dirFlag);
            player.running = true;
        }

        private void OnPathComplete(Character character, GameLocation location)
        {
            _monitor.Log($"PathFindController 回调: 到达目标", LogLevel.Debug);
        }

        /// <summary>计算从玩家到目标瓦片的朝向（0=上 1=右 2=下 3=左）。</summary>
        private static int GetDirectionTo(int tileX, int tileY)
        {
            int dx = tileX - (int)Game1.player.Tile.X;
            int dy = tileY - (int)Game1.player.Tile.Y;
            if (Math.Abs(dx) > Math.Abs(dy))
                return dx > 0 ? 1 : 3;
            return dy > 0 ? 2 : 0;
        }
    }

    /// <summary>ActionExecutor 的内部状态。</summary>
    public enum ActionState
    {
        Idle,
        Navigating,
        Interacting
    }
}
