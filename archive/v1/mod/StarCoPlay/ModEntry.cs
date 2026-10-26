using System;
using System.Collections.Generic;
using System.Linq;
using HarmonyLib;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StarCoPlay.Bridge;

namespace StarCoPlay
{
    /// <summary>
    /// Star CoPlay — Mod 入口。
    /// 职责：初始化各模块、挂载事件、协调 StateReader/ActionExecutor/Bridge。
    /// </summary>
    public class ModEntry : Mod
    {
        /*── 核心模块 ──*/
        private StateReader _state = null!;
        private ActionExecutor _actions = null!;
        private StuckDetector _stuckDetector = null!;

        /*── Bridge 模块（Agent↔Mod 文件桥） ──*/
        private BridgeManager _bridge = null!;
        private GameStateWriter _stateWriter = null!;
        private EventEmitter _eventEmitter = null!;
        private CommandProcessor _commandProcessor = null!;

        /*── 测试基础设施模块 ──*/
        private DebugReporter _reporter = null!;
        private TestCommands _commands = null!;
        private HudOverlay _hud = null!;
        private ChatCommandHandler _chatHandler = null!;

        /*── 配置 ──*/
        private ModConfig _config = null!;

        /*── 状态 ──*/
        private bool _demoRunning;
        private bool _bridgeActive;

        /// <summary>NPC 感知范围（tiles）。</summary>
        private const float NpcNearbyRadius = 10f;

        /// <summary>体力低预警阈值（百分比）。</summary>
        private const float EnergyLowPercent = 0.2f;

        /// <summary>上一次触发体力低警告时的体力值（避免重复推送）。</summary>
        private float _lastEnergyLowValue = float.MaxValue;

        /*────────────────────────────────────────────
         *  Entry — SMAPI 生命周期入口
         *────────────────────────────────────────────*/
        public override void Entry(IModHelper helper)
        {
            _config = helper.ReadConfig<ModConfig>();

            // 核心模块
            _state = new StateReader(Monitor);
            _actions = new ActionExecutor(Monitor);
            _stuckDetector = new StuckDetector(Monitor, _config.StuckThresholdTicks);

            // Bridge 模块
            _bridge = new BridgeManager(Monitor, helper.DirectoryPath);
            _eventEmitter = new EventEmitter(Monitor, _bridge);
            _stateWriter = new GameStateWriter(Monitor, _bridge, _state, _actions);
            _commandProcessor = new CommandProcessor(Monitor, _bridge, _eventEmitter, _actions, _state);

            // 测试基础设施
            _reporter = new DebugReporter(Monitor, helper.DirectoryPath);
            _actions.SetReporter(_reporter);
            _commands = new TestCommands(helper, Monitor, _state, _actions, _reporter, _eventEmitter);
            _hud = new HudOverlay(Monitor, _state, _actions, _reporter);
            _chatHandler = new ChatCommandHandler(Monitor, _state, _actions, _reporter);

            // Harmony — 拦截聊天框命令
            var harmony = new Harmony(ModManifest.UniqueID);
            harmony.PatchAll();

            // 核心事件
            helper.Events.GameLoop.DayStarted += OnDayStarted;
            helper.Events.GameLoop.UpdateTicked += OnUpdateTicked;
            helper.Events.GameLoop.OneSecondUpdateTicked += OnOneSecondTicked;
            helper.Events.GameLoop.TimeChanged += OnTimeChanged;
            helper.Events.Player.Warped += OnWarped;
            helper.Events.Player.InventoryChanged += OnInventoryChanged;
            helper.Events.GameLoop.SaveLoaded += OnSaveLoaded;
            helper.Events.GameLoop.DayEnding += OnDayEnding;
            helper.Events.GameLoop.ReturnedToTitle += OnReturnedToTitle;

            // HUD 事件
            helper.Events.Display.RenderedHud += _hud.OnRenderedHud;
            helper.Events.Input.ButtonPressed += _hud.OnButtonPressed;

            Monitor.Log("Star CoPlay v0.3.0 已加载 | F7 切换 HUD | 聊天框输入 /agent_help 查看命令", LogLevel.Info);
        }

        /*────────────────────────────────────────────
         *  存档加载 — 打印初始状态 + 启动报告会话
         *────────────────────────────────────────────*/
        private void OnSaveLoaded(object? sender, SaveLoadedEventArgs e)
        {
            _reporter.StartSession();

            // 启动文件桥
            _bridge.Initialize();
            _bridgeActive = true;
            _lastEnergyLowValue = float.MaxValue;

            Monitor.Log("=== 存档已加载，初始状态 ===", LogLevel.Debug);
            LogWorldState();
            LogPlayerState();
            Monitor.Log("提示: 按 F7 切换 HUD 面板 | 输入 agent_status 查看完整状态", LogLevel.Debug);
        }

        /*────────────────────────────────────────────
         *  每天开始 — 每日规划入口
         *────────────────────────────────────────────*/
        private void OnDayStarted(object? sender, DayStartedEventArgs e)
        {
            if (!Context.IsWorldReady) return;

            Monitor.Log($"=== 第 {Game1.dayOfMonth} 天开始 ({Game1.currentSeason} Y{Game1.year}) ===", LogLevel.Debug);
            LogWorldState();
            LogPlayerState();

            // 重置卡住检测
            _stuckDetector.Reset();
            _lastEnergyLowValue = float.MaxValue;

            // Bridge: 推送 day_started 事件
            if (_bridgeActive)
            {
                var world = _state.GetWorldState();
                var farm = _state.GetFarmSummary();
                _eventEmitter.EmitDayStarted(
                    world.Day, world.Season, world.Year, world.Weather,
                    farm?.UnwateredCount ?? 0, farm?.HarvestableCount ?? 0);
            }

            // Demo：启动每日自动行为
            if (_config.EnableDailyDemo)
            {
                _demoRunning = true;
                StartDailyDemo();
            }
        }

        /*────────────────────────────────────────────
         *  每 Tick — 高频执行循环（~60fps）
         *────────────────────────────────────────────*/
        private void OnUpdateTicked(object? sender, UpdateTickedEventArgs e)
        {
            if (!Context.IsWorldReady) return;

            // Bridge: 每帧轮询命令（独立于 Demo 模式）
            if (_bridgeActive)
            {
                _commandProcessor.Poll();
            }

            // 每秒一次更新卡住检测（仅在 Agent 有活跃动作时）
            if (e.IsMultipleOf(60))
            {
                if (_actions.IsBusy)
                    _stuckDetector.Update(Game1.player.Tile);
                else
                    _stuckDetector.Reset();
            }

            // 驱动当前动作（ActionExecutor 内部管理状态机）
            _actions.Tick();
        }

        /*────────────────────────────────────────────
         *  每秒 — 中频决策
         *────────────────────────────────────────────*/
        private void OnOneSecondTicked(object? sender, OneSecondUpdateTickedEventArgs e)
        {
            if (!Context.IsWorldReady) return;

            // Bridge: 每秒写状态快照 + 检查体力 + 扫描附近 NPC
            if (_bridgeActive)
            {
                _stateWriter.WriteSnapshot();
                CheckEnergyLow();
                ScanNearbyNpcs();
            }

            // 卡住检测（仅在 Agent 有活跃动作时才触发恢复）
            if (_stuckDetector.IsStuck && _actions.IsBusy)
            {
                var tile = Game1.player.Tile;
                Monitor.Log($"检测到卡住！位置=({(int)tile.X},{(int)tile.Y}) 持续={_stuckDetector.StuckTicks}ticks " +
                    $"ActionState={_actions.CurrentState} UsingTool={Game1.player.UsingTool} canMove={Game1.player.canMove}", LogLevel.Warn);
                _reporter.LogStuckEvent((int)tile.X, (int)tile.Y, _stuckDetector.StuckTicks);
                if (_bridgeActive)
                {
                    _eventEmitter.EmitAgentActionCancelled(
                        "stuck",
                        (int)tile.X,
                        (int)tile.Y,
                        _actions.CurrentState.ToString(),
                        _stuckDetector.StuckTicks);
                }
                _actions.CancelCurrent();
                _stuckDetector.Reset();
            }
        }

        /*────────────────────────────────────────────
         *  时间变化 — 游戏时间推进
         *────────────────────────────────────────────*/
        private void OnTimeChanged(object? sender, TimeChangedEventArgs e)
        {
            if (!Context.IsWorldReady) return;

            Monitor.Log($"时间: {e.NewTime} ({FormatTime(e.NewTime)})", LogLevel.Trace);

            // Bridge: 推送 time_changed 事件
            if (_bridgeActive)
            {
                _eventEmitter.EmitTimeChanged(e.NewTime, FormatTime(e.NewTime));
            }

            // 晚上 24:00 停止 Demo
            if (e.NewTime >= 2400 && _demoRunning)
            {
                Monitor.Log("天色已晚，停止 Demo 行为", LogLevel.Debug);
                _demoRunning = false;
                _actions.CancelCurrent();
            }
        }

        /*────────────────────────────────────────────
         *  换地图 — 上下文感知
         *────────────────────────────────────────────*/
        private void OnWarped(object? sender, WarpedEventArgs e)
        {
            if (!e.IsLocalPlayer) return;

            Monitor.Log($"地图切换: {e.OldLocation.Name} → {e.NewLocation.Name}", LogLevel.Debug);
            _stuckDetector.Reset();

            // Bridge: 推送 player_warped 事件
            if (_bridgeActive)
            {
                var playerTile = Game1.player.Tile;
                _eventEmitter.EmitPlayerWarped(e.OldLocation.Name, e.NewLocation.Name, (int)playerTile.X, (int)playerTile.Y);
            }
        }

        /*────────────────────────────────────────────
         *  背包变动
         *────────────────────────────────────────────*/
        private void OnInventoryChanged(object? sender, InventoryChangedEventArgs e)
        {
            if (!e.IsLocalPlayer) return;

            foreach (var item in e.Added)
                Monitor.Log($"  [背包+] {item.DisplayName} x{item.Stack}", LogLevel.Debug);
            foreach (var item in e.Removed)
                Monitor.Log($"  [背包-] {item.DisplayName} x{item.Stack}", LogLevel.Debug);

            // Bridge: 推送 inventory_changed 事件
            if (_bridgeActive)
            {
                _eventEmitter.EmitInventoryChanged(e.Added, e.Removed);
            }
        }

        /*────────────────────────────────────────────
         *  每天结束 — 自动保存报告
         *────────────────────────────────────────────*/
        private void OnDayEnding(object? sender, DayEndingEventArgs e)
        {
            // Bridge: 推送 day_ending 事件
            if (_bridgeActive)
            {
                _eventEmitter.EmitDayEnding(Game1.dayOfMonth, Game1.currentSeason);
            }

            var path = _reporter.GenerateReport();
            Monitor.Log($"每日报告已自动保存: {path}", LogLevel.Debug);
        }

        /*────────────────────────────────────────────
         *  退出存档 — 自动保存最终报告
         *────────────────────────────────────────────*/
        private void OnReturnedToTitle(object? sender, ReturnedToTitleEventArgs e)
        {
            // Bridge: 关闭文件桥
            if (_bridgeActive)
            {
                _bridge.Shutdown();
                _bridgeActive = false;
            }

            var path = _reporter.GenerateReport();
            Monitor.Log($"退出报告已自动保存: {path}", LogLevel.Debug);
        }

        /*────────────────────────────────────────────
         *  Demo 行为 — 简单的每日流程示例
         *────────────────────────────────────────────*/
        private void StartDailyDemo()
        {
            Monitor.Log("启动每日 Demo 行为...", LogLevel.Debug);

            // 读取农场状态
            var farmSummary = _state.GetFarmSummary();
            if (farmSummary != null)
            {
                Monitor.Log($"  农场概况: {farmSummary.CropCount} 株作物, {farmSummary.UnwateredCount} 株未浇水, {farmSummary.HarvestableCount} 株可收获", LogLevel.Debug);
            }

            // 尝试移动到农场中心
            if (Game1.currentLocation?.Name == "Farm")
            {
                var target = new Point(64, 15);
                Monitor.Log($"  导航至 ({target.X}, {target.Y})...", LogLevel.Debug);
                _actions.NavigateTo(target);
            }
        }

        /*────────────────────────────────────────────
         *  Bridge 辅助检测
         *────────────────────────────────────────────*/

        /// <summary>检测体力低于阈值，推送 energy_low 事件（仅在体力下降时触发，避免重复）。</summary>
        private void CheckEnergyLow()
        {
            float current = Game1.player.stamina;
            float max = Game1.player.maxStamina.Value;

            if (max <= 0) return;

            float percent = current / max;
            if (percent < EnergyLowPercent && current < _lastEnergyLowValue)
            {
                _eventEmitter.EmitEnergyLow(current, max);
                _lastEnergyLowValue = current;
            }
            else if (percent >= EnergyLowPercent)
            {
                // 体力恢复后重置，允许下次再触发
                _lastEnergyLowValue = float.MaxValue;
            }
        }

        /// <summary>扫描附近 NPC 并推送 npc_nearby 事件。</summary>
        private void ScanNearbyNpcs()
        {
            try
            {
                var player = Game1.player;
                var location = player.currentLocation;
                if (location == null) return;

                var currentNpcs = new List<string>();

                foreach (var npc in location.characters)
                {
                    if (npc == null || npc.IsInvisible) continue;

                    float dx = npc.Tile.X - player.Tile.X;
                    float dy = npc.Tile.Y - player.Tile.Y;
                    float distance = (float)Math.Sqrt(dx * dx + dy * dy);

                    if (distance <= NpcNearbyRadius)
                    {
                        currentNpcs.Add(npc.Name);
                        int hearts = player.getFriendshipHeartLevelForNPC(npc.Name);
                        var friendship = player.friendshipData.ContainsKey(npc.Name) ? player.friendshipData[npc.Name] : null;
                        bool talkedToday = friendship?.TalkedToToday ?? false;
                        bool giftedToday = (friendship?.GiftsToday ?? 0) > 0;
                        _eventEmitter.EmitNpcNearby(npc.Name, location.Name, (int)npc.Tile.X, (int)npc.Tile.Y, distance, hearts, talkedToday, giftedToday);
                    }
                }

                // 更新缓存：移除已离开的 NPC
                _eventEmitter.UpdateNpcCache(currentNpcs);
            }
            catch (Exception ex)
            {
                Monitor.Log($"ScanNearbyNpcs 异常: {ex.Message}\n{ex.StackTrace}", LogLevel.Error);
            }
        }

        /*────────────────────────────────────────────
         *  辅助日志方法
         *────────────────────────────────────────────*/
        private void LogWorldState()
        {
            var world = _state.GetWorldState();
            Monitor.Log($"  世界: {world.Season} Y{world.Year} D{world.Day} {FormatTime(world.Time)} | 天气: {world.Weather}", LogLevel.Debug);
        }

        private void LogPlayerState()
        {
            var player = _state.GetPlayerState();
            Monitor.Log($"  玩家: {player.Location} ({player.TileX},{player.TileY}) | 体力: {player.Energy}/{player.MaxEnergy} | 金币: {player.Gold}", LogLevel.Debug);
        }

        private static string FormatTime(int time) => TimeHelper.FormatTime(time);
    }

    /// <summary>共享时间格式化工具。</summary>
    public static class TimeHelper
    {
        public static string FormatTime(int time)
        {
            int hours = time / 100;
            int minutes = time % 100;
            string period = hours < 12 ? "AM" : "PM";
            int displayHour = hours > 12 ? hours - 12 : (hours == 0 ? 12 : hours);
            return $"{displayHour}:{minutes:D2} {period}";
        }
    }
}
