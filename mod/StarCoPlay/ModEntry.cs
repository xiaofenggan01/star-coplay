using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Threading;
using HarmonyLib;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.Menus;
using StarCoPlay.Bridge;
using StarCoPlay.Configuration;
using StarCoPlay.GameServices.Inventory;
using StarCoPlay.GameServices.Movement;
using StarCoPlay.GameServices.SimulatedInput;
using StarCoPlay.Integrations;
using StarCoPlay.Logging;
using StarCoPlay.V2;
using StarCoPlay.V2.Compound;
using StarCoPlay.V2.Models;
using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Dispatch;
using StarCoPlay.ProtocolCore.Handlers;
using StarCoPlay.ProtocolCore.Results;

namespace StarCoPlay
{
    /// <summary>
    /// Star CoPlay — Mod 入口。
    /// 职责：初始化各模块、挂载事件、协调 StateReader/ActionExecutor/Bridge。
    /// </summary>
    public class ModEntry : Mod
    {
        /*── 日志模块 ──*/
        private ModLogger _logger = null!;

        /*── 核心模块 ──*/
        private StateReader _state = null!;
        private ActionExecutor _actions = null!;
        private StuckDetector _stuckDetector = null!;

        /*── Bridge 模块（Agent↔Mod 文件桥） ──*/
        private IBridge _bridge = null!;
        private GameStateWriter _stateWriter = null!;
        private EventEmitter _eventEmitter = null!;
        private CommandProcessor _commandProcessor = null!;
        private AdapterV2 _adapterV2 = null!;
        private MoveToHandler _moveToHandler = null!;
        // 模拟输入执行原子切换：启用后 move_to 走当前原子实现，否则走旧兼容实现。
        // CommandProcessor 和 GoToHandler 均走此切换（go_to L2 重设计后不再依赖旧 MoveToHandler）。
        private IMoveToHandler _moveToHandlerForProcessor = null!;
        private DoHandler _doHandler = null!;
        private UseToolHandler _useToolHandler = null!;
        private AnimationAwaiter _animAwaiter = null!;
        private CompoundDispatcher _compoundDispatcher = null!;

        /*── 测试基础设施模块 ──*/
        private DebugReporter _reporter = null!;
        private TestCommands _commands = null!;
        private HudOverlay _hud = null!;
        private ChatCommandHandler _chatHandler = null!;

        /*── L0 InputBridge 层 ──*/
        private InputBridge _inputBridge = null!;
        private InputCombo _inputCombo = null!;
        private MenuNavigator _menuNavigator = null!;

        /*── Proto-native 执行基座 ──*/
        private StarCoPlay.ProtocolCore.Dispatch.ProtoHandlerRegistry _protoRegistry = null!;
        private StarCoPlay.ProtocolCore.Dispatch.ProtoCommandDispatcher _protoDispatcher = null!;
        private StarCoPlay.ProtocolCore.Dispatch.ProtoCommandScheduler _protoScheduler = null!;
        private StarCoPlay.ProtocolCore.Results.ProtoCommandResultFactory _protoResultFactory = null!;

        /*── 卡住恢复 ──*/
        private StuckRecovery _stuckRecovery = null!;

        /*── 配置 ──*/
        private ModConfig _config = null!;

        /*── 配置热重载协调器 ──*/
        private ConfigReloadCoordinator _reloadCoordinator = null!;

        /*── 文件型本地控制面 ──*/
        private LocalReloadPortal? _localPortal;
        private int _portalTickCounter;

        /*── WebSocket 状态 ──*/
        private WsBridge? _wsBridgeRef;
        private IProtocolBridge? _protocolBridgeRef;
        private AppearanceSync? _appearanceSync;
        private volatile bool _needBridgeRebuild;
        private int _pendingAppearanceSync;
        private int _pendingHostedControlRebase;
        private volatile string? _pendingChatMessage;
        private volatile WsConnectionState _prevWsState;
        private string? _gameInstanceNonce;

        /*── 状态 ──*/
        private bool _demoRunning;
        private bool _bridgeActive;
        /// <summary>当前实例是否为 AI 控制的角色（本机玩家 == TargetPlayerId）。</summary>
        private bool _isAIControlledInstance;

        private bool IsDevMode => string.Equals(_config?.PlatformToken?.Trim(), "dev", StringComparison.OrdinalIgnoreCase);

        /// <summary>NPC 感知范围（tiles）。</summary>
        private const float NpcNearbyRadius = 10f;

        /// <summary>体力低预警阈值（百分比）。</summary>
        private const float EnergyLowPercent = 0.2f;

        /// <summary>上一次触发体力低警告时的体力值（避免重复推送）。</summary>
        private float _lastEnergyLowValue = float.MaxValue;

        /// <summary>是否当前有活跃的对话框（用于去重 dialogue_opened 事件）。</summary>
        private bool _dialogueActive;

        /// <summary>上一次检测到的对话框文本（去重用，同一个对话框只推送一次）。</summary>
        private string _lastDialogueText = "";

        /*────────────────────────────────────────────
         *  Entry — SMAPI 生命周期入口
         *────────────────────────────────────────────*/
        public override void Entry(IModHelper helper)
        {
            _config = helper.ReadConfig<ModConfig>();
            ApplyConfigMigrations(helper);

            // 日志模块
            _logger = new ModLogger(Monitor, Helper.DirectoryPath, _config.EnableFileLogging, _config.MaxLogFileSizeMB);

            // 核心模块
            _state = new StateReader(Monitor);
            _actions = new ActionExecutor(Monitor);
            _stuckDetector = new StuckDetector(Monitor, _config.StuckThresholdTicks);
            _stuckDetector.SetLogger(_logger);
            _stuckRecovery = new StuckRecovery(Monitor, _actions);
            _stuckRecovery.SetLogger(_logger);

            // L0 InputBridge 层
            _inputBridge = new InputBridge(Helper.Input, Monitor);
            _inputCombo = new InputCombo(_inputBridge, Monitor);
            _menuNavigator = new MenuNavigator(_inputBridge, Monitor);

            // Proto-native 执行基座
            _protoResultFactory = new ProtoCommandResultFactory();
            _protoRegistry = new ProtoHandlerRegistry();
            _protoRegistry.Register(new FaceProtoHandler(_inputCombo, _inputBridge, Monitor));
            _protoRegistry.Register(new EquipProtoHandler(_inputCombo, _inputBridge, Monitor));
            _protoRegistry.Register(new OpenMenuProtoHandler(_inputCombo, _inputBridge, Monitor));
            _protoRegistry.Register(new QueryMenuProtoHandler(_menuNavigator, Monitor));
            _protoRegistry.Register(new MenuClickProtoHandler(_menuNavigator, _inputBridge, Monitor));
            _protoRegistry.Register(new MenuCloseProtoHandler(_inputCombo, _inputBridge, Monitor));

            var tileActionService = new GameServices.World.TileActionService(_inputCombo, _inputBridge, Monitor);
            var socialActionService = new GameServices.Social.SocialActionService(Monitor);
            var interactProtoHandler = new InteractProtoHandler(tileActionService, _inputBridge, Monitor);
            _protoRegistry.Register(interactProtoHandler);
            _protoRegistry.Register(new UseToolProtoHandler(tileActionService, _inputBridge, Monitor));
            _protoRegistry.Register(new TpProtoHandler(Monitor));
            var moveToProtoHandler = new MoveToProtoHandler(_actions, _inputCombo, _inputBridge, Monitor);
            _protoRegistry.Register(moveToProtoHandler);
            var goToProtoHandler = new GoToProtoHandler(moveToProtoHandler, interactProtoHandler, Monitor);
            _protoRegistry.Register(goToProtoHandler);

            // go_to_bed composite handler
            var sleepBedScanner = new GameServices.Sleep.SleepBedScanner(Monitor);
            var sleepStateObserver = new GameServices.Sleep.SleepStateObserver();
            var endOfDayUiDriver = new GameServices.Sleep.EndOfDayUiDriver(Monitor);
            _protoRegistry.Register(new GoToBedProtoHandler(
                goToProtoHandler, interactProtoHandler, _inputBridge, _menuNavigator,
                sleepBedScanner, sleepStateObserver, endOfDayUiDriver, Monitor));

            _protoRegistry.Register(new SayProtoHandler(socialActionService, Monitor));
            _protoRegistry.Register(new EmoteProtoHandler(socialActionService, Monitor));

            // Query capabilities — proto-native path
            _protoRegistry.Register(new QueryWorldRegionProtoHandler(Monitor));
            _protoRegistry.Register(new QueryRuntimeSnapshotProtoHandler(Monitor));
            _protoRegistry.Register(new QueryInventorySnapshotProtoHandler(Monitor));
            _protoRegistry.Register(new QueryUiSnapshotProtoHandler(Monitor));
            var protoRefIdResolver = new V2.RefId.Resolution.RefIdResolverDispatcher();
            protoRefIdResolver.Register("world_entity", new V2.RefId.Resolution.WorldEntityResolver());
            protoRefIdResolver.Register("character", new V2.RefId.Resolution.CharacterResolver());
            protoRefIdResolver.Register("inventory", new V2.RefId.Resolution.InventoryResolver());
            protoRefIdResolver.Register("ui_element", new V2.RefId.Resolution.UiElementResolver());
            _protoRegistry.Register(new QueryInspectRefsProtoHandler(protoRefIdResolver, Monitor));

            // ProtoCommandScheduler 和 ProtoCommandDispatcher 在 OnSaveLoaded 中初始化
            // （需要 CommandProcessor 实例来判断 legacy busy）

            // V2 Adapter — capability registration
            // AnimationAwaiter 持有 InputBridge 引用，Tick 时优先检查 InputBridge.IsBusy，
            // 在 V2 Handler 入队的按键还没消化完时不会判定完成。
            _animAwaiter = new AnimationAwaiter(Monitor, _inputBridge);
            _adapterV2 = new AdapterV2(Monitor);
            _adapterV2.ModVersion = ModManifest.Version.ToString();
            _adapterV2.Register(AdapterV2.CapabilityObserve, cmd =>
                ObserveHandler.Handle(cmd));
            // move_to — 旧 L1 MoveToHandler 保留（BT 快环等可能通过 CommandProcessor 直接调用）
            _moveToHandler = new MoveToHandler(_actions, Monitor);
            // CommandProcessor 走的 L1 实现按 UseInputBridgeHandlers 切换
            if (_config.UseInputBridgeHandlers)
            {
                var moveToAtom = new StarCoPlay.Capabilities.ExecutionAtoms.MoveToHandler(
                    _actions, _inputCombo, _inputBridge, _animAwaiter, Monitor);
                _moveToHandlerForProcessor = moveToAtom;
                _adapterV2.Register(AdapterV2.CapabilityMoveTo, moveToAtom.Handle);
                Monitor.Log("[ExecutionAtom] move_to -> simulated input handler (modes A/B/C)", LogLevel.Info);
            }
            else
            {
                _moveToHandlerForProcessor = _moveToHandler;
                _adapterV2.Register(AdapterV2.CapabilityMoveTo, _moveToHandler.Handle);
            }
            // face — 根据 UseInputBridgeHandlers 切换新旧实现
            if (_config.UseInputBridgeHandlers)
            {
                var faceAtom = new StarCoPlay.Capabilities.ExecutionAtoms.FaceHandler(_inputCombo, _animAwaiter, Monitor);
                _adapterV2.Register(AdapterV2.CapabilityFace, faceAtom.Handle);
                Monitor.Log("[ExecutionAtom] face -> simulated input handler", LogLevel.Info);
            }
            else
            {
                var faceHandler = new FaceHandler(Monitor);
                _adapterV2.Register(AdapterV2.CapabilityFace, faceHandler.Handle);
            }

            // do / interact — V2 模式下额外注册 interact capability，旧 do 保留以便兼容
            if (_config.UseInputBridgeHandlers)
            {
                var interactAtom = new StarCoPlay.Capabilities.ExecutionAtoms.InteractHandler(_inputCombo, _animAwaiter, Monitor);
                // AdapterV2 暂未定义 CapabilityInteract 常量，先用字符串字面量 "interact"
                _adapterV2.Register("interact", interactAtom.Handle);

                // 旧 do 仍保留（V2 期间兼容旧调用方）
                _doHandler = new DoHandler(_actions, Monitor);
                var interactHandler = new InteractHandler(_actions, _state, Monitor, _animAwaiter);
                _doHandler.SetInteractHandler(interactHandler);
                _adapterV2.Register(AdapterV2.CapabilityDo, _doHandler.Handle);

                Monitor.Log("[ExecutionAtom] interact -> simulated input handler; legacy do remains for compatibility", LogLevel.Info);
            }
            else
            {
                _doHandler = new DoHandler(_actions, Monitor);
                var interactHandler = new InteractHandler(_actions, _state, Monitor, _animAwaiter);
                _doHandler.SetInteractHandler(interactHandler);
                _adapterV2.Register(AdapterV2.CapabilityDo, _doHandler.Handle);
            }

            // use_tool — 根据 UseInputBridgeHandlers 切换新旧实现
            if (_config.UseInputBridgeHandlers)
            {
                var useToolAtom = new StarCoPlay.Capabilities.ExecutionAtoms.UseToolHandler(_inputCombo, _animAwaiter, Monitor);
                _adapterV2.Register(AdapterV2.CapabilityUseTool, useToolAtom.Handle);
                Monitor.Log("[ExecutionAtom] use_tool -> simulated input handler", LogLevel.Info);
            }
            else
            {
                _useToolHandler = new UseToolHandler(_actions, Monitor, _animAwaiter);
                _adapterV2.Register(AdapterV2.CapabilityUseTool, _useToolHandler.Handle);
            }

            // equip — 根据 UseInputBridgeHandlers 切换新旧实现
            if (_config.UseInputBridgeHandlers)
            {
                var equipAtom = new StarCoPlay.Capabilities.ExecutionAtoms.EquipHandler(_inputCombo, _actions, Monitor);
                _adapterV2.Register(AdapterV2.CapabilityEquip, equipAtom.Handle);
                Monitor.Log("[ExecutionAtom] equip -> simulated input handler with compatibility fallback", LogLevel.Info);
            }
            else
            {
                var equipHandler = new EquipHandler(_actions, Monitor);
                _adapterV2.Register(AdapterV2.CapabilityEquip, equipHandler.Handle);
            }

            // V2 菜单 capability — 仅 V2 模式下注册（旧版没有这些）
            if (_config.UseInputBridgeHandlers)
            {
                var openMenu = new StarCoPlay.Capabilities.ExecutionAtoms.OpenMenuHandler(_inputCombo, _animAwaiter, Monitor);
                _adapterV2.Register("open_menu", openMenu.Handle);

                var menuClick = new StarCoPlay.Capabilities.ExecutionAtoms.MenuClickHandler(_menuNavigator, _animAwaiter, Monitor);
                _adapterV2.Register("menu_click", menuClick.Handle);

                var menuClose = new StarCoPlay.Capabilities.ExecutionAtoms.MenuCloseHandler(_inputCombo, _animAwaiter, Monitor);
                _adapterV2.Register("menu_close", menuClose.Handle);

                Monitor.Log("[ExecutionAtom] registered menu capabilities: open_menu/menu_click/menu_close", LogLevel.Info);
            }

            Monitor.Log($"[Config] UseInputBridgeHandlers = {_config.UseInputBridgeHandlers}", LogLevel.Info);
            var sayHandler = new SayHandler(_actions, Monitor);
            _adapterV2.Register(AdapterV2.CapabilitySay, sayHandler.Handle);
            var emoteHandler = new EmoteHandler(_actions, Monitor);
            _adapterV2.Register(AdapterV2.CapabilityEmote, emoteHandler.Handle);

            // tp — teleport to a map location (fallback when move_to fails with NO_PATH)
            var tpHandler = new TpHandler(_actions, Monitor);
            _adapterV2.Register(AdapterV2.CapabilityTp, tpHandler.Handle);

            // Compound capabilities (multi-step state-machine handlers)
            _compoundDispatcher = new CompoundDispatcher(Monitor);
            var waterHandler = new WaterCropsHandler(_actions, Monitor);
            _compoundDispatcher.Register(AdapterV2.CapabilityWaterCrops, waterHandler);
            _adapterV2.Register(AdapterV2.CapabilityWaterCrops, cmd =>
            {
                return _compoundDispatcher.Start(AdapterV2.CapabilityWaterCrops, cmd);
            });
            var harvestHandler = new HarvestCropsHandler(_actions, Monitor);
            _compoundDispatcher.Register(AdapterV2.CapabilityHarvestCrops, harvestHandler);
            _adapterV2.Register(AdapterV2.CapabilityHarvestCrops, cmd =>
            {
                return _compoundDispatcher.Start(AdapterV2.CapabilityHarvestCrops, cmd);
            });
            var goBedHandler = new GoBedHandler(_actions, Monitor);
            _compoundDispatcher.Register(AdapterV2.CapabilityGoBed, goBedHandler);
            _adapterV2.Register(AdapterV2.CapabilityGoBed, cmd =>
            {
                return _compoundDispatcher.Start(AdapterV2.CapabilityGoBed, cmd);
            });
            var shipItemsHandler = new ShipItemsHandler(_actions, Monitor);
            _compoundDispatcher.Register(AdapterV2.CapabilityShipItems, shipItemsHandler);
            _adapterV2.Register(AdapterV2.CapabilityShipItems, cmd =>
            {
                return _compoundDispatcher.Start(AdapterV2.CapabilityShipItems, cmd);
            });

            var buyItemsHandler = new BuyItemsHandler(_actions, Monitor);
            _compoundDispatcher.Register(AdapterV2.CapabilityBuyItems, buyItemsHandler);
            _adapterV2.Register(AdapterV2.CapabilityBuyItems, cmd =>
            {
                return _compoundDispatcher.Start(AdapterV2.CapabilityBuyItems, cmd);
            });

            var plantCropsHandler = new PlantCropsHandler(_actions, Monitor);
            _compoundDispatcher.Register(AdapterV2.CapabilityPlantCrops, plantCropsHandler);
            _adapterV2.Register(AdapterV2.CapabilityPlantCrops, cmd =>
            {
                return _compoundDispatcher.Start(AdapterV2.CapabilityPlantCrops, cmd);
            });

            var chopTreesHandler = new ChopTreesHandler(_actions, Monitor);
            _compoundDispatcher.Register(AdapterV2.CapabilityChopTrees, chopTreesHandler);
            _adapterV2.Register(AdapterV2.CapabilityChopTrees, cmd =>
            {
                return _compoundDispatcher.Start(AdapterV2.CapabilityChopTrees, cmd);
            });

            var mineRocksHandler = new MineRocksHandler(_actions, Monitor);
            _compoundDispatcher.Register(AdapterV2.CapabilityMineRocks, mineRocksHandler);
            _adapterV2.Register(AdapterV2.CapabilityMineRocks, cmd =>
            {
                return _compoundDispatcher.Start(AdapterV2.CapabilityMineRocks, cmd);
            });

            var executeOnTargetsHandler = new ExecuteOnTargetsHandler(_actions, Monitor);
            _compoundDispatcher.Register(AdapterV2.CapabilityExecuteOnTargets, executeOnTargetsHandler);
            _adapterV2.Register(AdapterV2.CapabilityExecuteOnTargets, cmd =>
            {
                return _compoundDispatcher.Start(AdapterV2.CapabilityExecuteOnTargets, cmd);
            });

            // menu_interact — L2 统一菜单交互（声明式事务执行器）
            var menuInteractHandler = new MenuInteractHandler(_actions, buyItemsHandler, Monitor);
            _compoundDispatcher.Register(AdapterV2.CapabilityMenuInteract, menuInteractHandler);
            _adapterV2.Register(AdapterV2.CapabilityMenuInteract, cmd =>
            {
                return _compoundDispatcher.Start(AdapterV2.CapabilityMenuInteract, cmd);
            });

            // go_to — L2 语义导航（名称解析 + 跨地图路由 + 目标追踪）
            var goToHandler = new GoToHandler(_moveToHandlerForProcessor, _actions, Monitor);
            _compoundDispatcher.Register(AdapterV2.CapabilityGoTo, goToHandler);
            _adapterV2.Register(AdapterV2.CapabilityGoTo, cmd =>
            {
                return _compoundDispatcher.Start(AdapterV2.CapabilityGoTo, cmd);
            });

            // TEMP: test_input — L0 InputBridge 端到端测试命令
            // 用于验证 InputBridge / InputCombo / MenuNavigator 4 种交互模式
            // 验证完成后应删除（capability 名称、注册逻辑、辅助方法 GetInt/GetString/GetButton）
            _adapterV2.Register("test_input", HandleTestInputCommand);

            // dev_eval — Dev 模式运行时反射 REPL（仅 PlatformToken=="dev" 时启用）
            if (IsDevMode)
            {
                var devEvalHandler = new DevEvalHandler(Monitor);
                _compoundDispatcher.Register(AdapterV2.CapabilityDevEval, devEvalHandler);
                _adapterV2.Register(AdapterV2.CapabilityDevEval, cmd =>
                {
                    return _compoundDispatcher.Start(AdapterV2.CapabilityDevEval, cmd);
                });
                Monitor.Log("[Dev] dev_eval REPL 已启用（PlatformToken=dev）", LogLevel.Info);
            }

            // Bridge 模块（使用玩家唯一 ID 隔离 bridge 目录）
            ulong? playerUniqueId = Context.IsWorldReady && Game1.player != null
                ? (ulong?)Game1.player.UniqueMultiplayerID
                : null;
            _bridge = CreateBridge(helper.DirectoryPath, playerUniqueId);
            _bridge.SetLogger(_logger);
            _eventEmitter = new EventEmitter(Monitor, _logger, _bridge, _config);

            _stateWriter = new GameStateWriter(Monitor, _bridge, _state, _actions);
            _commandProcessor = new CommandProcessor(Monitor, _logger, _bridge, _eventEmitter, _actions, _state, _adapterV2, _moveToHandlerForProcessor, _animAwaiter, _compoundDispatcher, playerUniqueId ?? 0, IsDevMode);

            // 测试基础设施
            _reporter = new DebugReporter(Monitor, helper.DirectoryPath);
            _actions.SetReporter(_reporter);
            _commands = new TestCommands(helper, Monitor, _state, _actions, _reporter, _eventEmitter, _config.PlatformToken, () => _commandProcessor);
            _hud = new HudOverlay(Monitor, helper.Translation, _state, _actions, _commandProcessor,
                _stuckDetector, _stuckRecovery, () => _bridgeActive, () => _wsBridgeRef,
                BuildIndicatorContext);
            _chatHandler = new ChatCommandHandler(Monitor, _state, _actions, _reporter, helper,
                () => _config,
                () => helper.WriteConfig(_config),
                () => _wsBridgeRef,
                () => _needBridgeRebuild = true,
                TriggerGoBed,
                () => HandleReload());

            // 配置热重载协调器（在 CommandProcessor 创建后初始化）
            _reloadCoordinator = new ConfigReloadCoordinator(Monitor);

            // 文件型本地控制面（供 Runtime Manager/Agent 无需游戏焦点触发重载）
            _localPortal = new LocalReloadPortal(Monitor);

            // 注册始终可用的 SMAPI 控制台命令 starcoplay_reload
            helper.ConsoleCommands.Add("starcoplay_reload",
                "重载 config.json 并重建 Bridge 连接（与 /agent_reload 共用逻辑）",
                (_, _) => HandleReload());

            // Harmony — 拦截聊天框命令
            var harmony = new Harmony(ModManifest.UniqueID);
            harmony.PatchAll();

            // 核心事件
            helper.Events.GameLoop.DayStarted += OnDayStarted;
            helper.Events.GameLoop.UpdateTicking += OnUpdateTicking_InputBridge;
            helper.Events.GameLoop.UpdateTicked += OnUpdateTicked;
            helper.Events.GameLoop.OneSecondUpdateTicked += OnOneSecondTicked;
            helper.Events.GameLoop.TimeChanged += OnTimeChanged;
            helper.Events.Player.Warped += OnWarped;
            helper.Events.Player.InventoryChanged += OnInventoryChanged;
            helper.Events.GameLoop.SaveLoaded += OnSaveLoaded;
            helper.Events.GameLoop.DayEnding += OnDayEnding;
            helper.Events.GameLoop.ReturnedToTitle += OnReturnedToTitle;
            helper.Events.Multiplayer.ModMessageReceived += OnModMessageReceived;

            // SMAPI 4.0 无 GameExiting 事件，用 ProcessExit 兜底确保 WsBridge 关闭
            AppDomain.CurrentDomain.ProcessExit += (_, _) => OnProcessExit();

            // HUD 事件
            helper.Events.Display.RenderedHud += _hud.OnRenderedHud;
            helper.Events.Input.ButtonPressed += _hud.OnButtonPressed;

            // GMCM 注册（GameLaunched 时机确保 GMCM 已加载）
            helper.Events.GameLoop.GameLaunched += OnGameLaunched;

            Monitor.Log("Star CoPlay v0.3.0 已加载 | F7 切换 HUD | 聊天框输入 /agent_help 查看命令", LogLevel.Info);
        }

        /// <summary>
        /// 升级旧版 config.json：曾默认关闭的模拟输入执行原子在填写平台 Token 后自动开启。
        /// </summary>
        private void ApplyConfigMigrations(IModHelper helper)
        {
            if (_config.UseInputBridgeHandlers || string.IsNullOrWhiteSpace(_config.PlatformToken))
                return;

            _config.UseInputBridgeHandlers = true;
            helper.WriteConfig(_config);
            Monitor.Log(
                "[Config] 已自动启用 UseInputBridgeHandlers（检测到平台 Token；云端 / Protocol 2.4.5 需要模拟输入执行原子）",
                LogLevel.Info);
        }

        private IBridge CreateBridge(string modDir, ulong? playerUniqueId)
        {
            if (LocalDirectBridge.ShouldSelectForMode(
                _config.BridgeMode,
                LocalDirectBridge.IsSelectableForThisInstance()))
            {
                var local = new LocalDirectBridge(Monitor, modDir);
                _wsBridgeRef = null;
                _protocolBridgeRef = local;
                Monitor.Log($"Bridge route selected: local_direct (mode={_config.BridgeMode})", LogLevel.Info);
                return local;
            }

            var modVer = ModManifest.Version.ToString();
            var url = _config.EffectiveWsUrl();
            _gameInstanceNonce ??= Guid.NewGuid().ToString("N");
            var capabilityEntries = HostedRouteHello.ProductionCapabilityEntries(
                _protoRegistry.RegisteredCapabilities,
                _adapterV2.RegisteredCapabilities);
            var routeHello = new HostedRouteHello(_gameInstanceNonce, capabilityEntries);
            var controlSnapshot = AgentControlSnapshot.FromConfig(_config);
            var wsBridge = new WsBridge(Monitor, modDir, url, _config.PlatformToken ?? "", modVer,
                playerUniqueId, routeHello, controlSnapshot);
            _wsBridgeRef = wsBridge;
            _protocolBridgeRef = wsBridge;
            wsBridge.ConnectionStateChanged += OnWsConnectionStateChanged;
            wsBridge.HostedStatusReceived += OnWsHostedStatusReceived;
            Monitor.Log($"Bridge route selected: ws (mode={_config.BridgeMode})", LogLevel.Info);
            return wsBridge;
        }

        /*────────────────────────────────────────────
         *  GMCM 注册
         *────────────────────────────────────────────*/
        private void OnGameLaunched(object? sender, GameLaunchedEventArgs e)
        {
            GmcmIntegration.Register(
                Helper, ModManifest,
                getConfig: () => _config,
                saveConfig: () => Helper.WriteConfig(_config),
                onConfigChanged: () => _needBridgeRebuild = true,
                getWsBridge: () => _wsBridgeRef
            );

            // P0 fix: IsActive Harmony patch（必须在 GameLaunched 之后 apply）
            // 已知限制：MonoGame/SDV 在 macOS 失焦时不处理键盘输入循环，
            // IsActive Harmony patch 已实测无法解决（SDV 处理 keyboard 时不查 IsActive）。
            // 失焦运行：通过 InputBridgeSimulator 总开关 + InputBridge 反射 OverrideButton/ApplyOverrides
            // 路径解决（详见 v2/mod/StarCoPlay/V2/Input/InputBridge.cs 注释）。
        }

        /*────────────────────────────────────────────
         *  WsBridge 状态变更 → 聊天框通知
         *────────────────────────────────────────────*/
        private void OnWsConnectionStateChanged(WsConnectionState state, string? reason)
        {
            if (state == WsConnectionState.Connected)
            {
                Interlocked.Exchange(ref _pendingAppearanceSync, 1);
                Interlocked.Exchange(ref _pendingHostedControlRebase, 1);
            }

            string? msg = state switch
            {
                WsConnectionState.AuthError => ConnectionMessages.ForAuthError(reason),
                WsConnectionState.SessionEnded => ConnectionMessages.ForSessionEnd(reason),
                WsConnectionState.Connected when _prevWsState == WsConnectionState.Reconnecting
                    => ConnectionMessages.ReconnectSuccess,
                _ => null
            };
            if (msg != null)
                Interlocked.Exchange(ref _pendingChatMessage, msg);
            _prevWsState = state;
        }

        private void OnWsHostedStatusReceived(string code, bool retryable)
        {
            if (code == "controller_recovery_required" && retryable)
                Interlocked.Exchange(ref _pendingHostedControlRebase, 1);

            var message = code switch
            {
                "controller_recovery_required" => "AI 控制端正在恢复，请稍候再试",
                "controller_reconnecting" => "AI 控制端正在恢复，请稍候再试",
                "player_reply_degraded" => "AI 本次没有成功回复，请稍后重试",
                _ => null,
            };
            if (message != null)
                Interlocked.Exchange(ref _pendingChatMessage, message);
        }

        private void RebaseStaleHostedControllerClaim()
        {
            var ws = _wsBridgeRef;
            var player = Game1.player;
            if (ws == null || player == null)
                return;

            AgentControlSnapshot local;
            try
            {
                local = AgentControlSnapshot.FromConfig(_config);
            }
            catch (Exception ex)
            {
                Monitor.Log($"Hosted control snapshot invalid; stale revision recovery skipped: {ex.Message}", LogLevel.Warn);
                return;
            }

            if (!local.TryRebaseControllerClaim(
                    (ulong)player.UniqueMultiplayerID,
                    ws.ConnectionRole,
                    ws.AcceptedAgentControlRevision,
                    ws.ControllerRecoveryAllowed,
                    out var rebased))
                return;

            rebased.ApplyTo(_config);
            Helper.WriteConfig(_config);
            _needBridgeRebuild = true;
            Monitor.Log(
                $"Hosted controller claim rebased: player={rebased.TargetPlayerId} revision={local.Revision}->{rebased.Revision}",
                LogLevel.Info);
        }

        /*────────────────────────────────────────────
         *  /agent_gn — 强制回家睡觉（通过 CompoundDispatcher 触发 go_bed）
         *────────────────────────────────────────────*/
        private bool TriggerGoBed()
        {
            if (_goBedPhase != GoBedPhase.Inactive)
            {
                Monitor.Log("[agent_gn] Already in progress", LogLevel.Warn);
                return false;
            }

            var player = Game1.player;
            if (player == null) return false;

            // 已在家 → 直接进入 Sleeping 阶段
            if (IsAtHome())
            {
                _goBedTicks = 0;
                _goBedPhase = GoBedPhase.Sleeping;
                Monitor.Log("[agent_gn] Already at home, entering Sleep phase", LogLevel.Info);
                return true;
            }

            // 传送回家
            var homeName = player.homeLocation.Value ?? "FarmHouse";
            if (!_actions.WarpTo(homeName, 22, 5))
            {
                Monitor.Log($"[agent_gn] WarpTo {homeName} failed", LogLevel.Warn);
                return false;
            }

            _goBedTicks = 0;
            _goBedPhase = GoBedPhase.WaitingForWarp;
            Monitor.Log($"[agent_gn] Warping to {homeName}, entering WaitingForWarp phase", LogLevel.Info);
            return true;
        }

        // ── /agent_gn 状态机（L2 特例：帧驱动，5 级递进重试）──
        private enum GoBedPhase { Inactive, WaitingForWarp, Sleeping, WaitingForDialog }
        private GoBedPhase _goBedPhase = GoBedPhase.Inactive;
        private int _goBedTicks;
        private StardewValley.Objects.BedFurniture? _goBedRef;

        /// <summary>每帧驱动 /agent_gn 状态机。</summary>
        private void TickGoBed()
        {
            if (_goBedPhase == GoBedPhase.Inactive) return;
            if (Game1.currentLocation == null || Game1.player == null) return;

            switch (_goBedPhase)
            {
                case GoBedPhase.WaitingForWarp:
                    TickGoBed_WaitWarp();
                    break;
                case GoBedPhase.Sleeping:
                    TickGoBed_Sleep();
                    break;
                case GoBedPhase.WaitingForDialog:
                    TickGoBed_WaitDialog();
                    break;
            }
        }

        private bool IsAtHome()
        {
            var homeName = Game1.player?.homeLocation.Value ?? "FarmHouse";
            var cur = Game1.currentLocation?.NameOrUniqueName ?? "";
            return cur.Equals(homeName, StringComparison.OrdinalIgnoreCase)
                || Game1.currentLocation is StardewValley.Locations.FarmHouse;
        }

        private void TickGoBed_WaitWarp()
        {
            _goBedTicks++;
            if (IsAtHome())
            {
                Monitor.Log($"[agent_gn] Arrived home (waited {_goBedTicks} ticks)", LogLevel.Debug);
                _goBedTicks = 0;
                _goBedPhase = GoBedPhase.Sleeping;
                return;
            }
            if (_goBedTicks > 300) // 5 秒超时
            {
                Monitor.Log("[agent_gn] Warp timeout, aborting", LogLevel.Error);
                _goBedPhase = GoBedPhase.Inactive;
            }
        }

        private void TickGoBed_Sleep()
        {
            var location = Game1.currentLocation;
            if (location == null || !IsAtHome())
            {
                Monitor.Log("[agent_gn] Not at home in Sleep phase, aborting", LogLevel.Error);
                _goBedPhase = GoBedPhase.Inactive;
                return;
            }

            // 找床
            _goBedRef = location.furniture
                .OfType<StardewValley.Objects.BedFurniture>()
                .FirstOrDefault(b => b.bedType == StardewValley.Objects.BedFurniture.BedType.Double)
                ?? location.furniture
                    .OfType<StardewValley.Objects.BedFurniture>()
                    .FirstOrDefault();

            if (_goBedRef != null)
            {
                // 定位到床上
                float posX = _goBedRef.TileLocation.X * 64f + 64f;
                float posY = _goBedRef.TileLocation.Y * 64f + 64f;
                Game1.player!.Position = new Microsoft.Xna.Framework.Vector2(posX, posY);
                Game1.player.faceDirection(0);
                // V1 经验：先不设 isInBed，直接 checkForAction
                _goBedRef.checkForAction(Game1.player);
                Monitor.Log($"[agent_gn] On bed ({posX / 64f:F0},{posY / 64f:F0}), checkForAction called", LogLevel.Info);
            }
            else if (location is StardewValley.Locations.FarmHouse fh)
            {
                var spot = fh.getBedSpot();
                Game1.player!.setTileLocation(new Microsoft.Xna.Framework.Vector2(spot.X, spot.Y));
                _actions.InteractWithTile(spot.X, spot.Y);
                Monitor.Log($"[agent_gn] Fallback getBedSpot ({spot.X},{spot.Y})", LogLevel.Warn);
            }
            else
            {
                Monitor.Log("[agent_gn] No bed found", LogLevel.Error);
                _goBedPhase = GoBedPhase.Inactive;
                return;
            }

            _goBedTicks = 0;
            _goBedPhase = GoBedPhase.WaitingForDialog;
        }

        private void TickGoBed_WaitDialog()
        {
            _goBedTicks++;

            // ── 级别 1：answerDialogueAction("Sleep_Yes") ──
            try
            {
                if (Game1.currentLocation?.answerDialogueAction("Sleep_Yes", null) == true)
                {
                    if (Game1.activeClickableMenu is StardewValley.Menus.DialogueBox)
                        Game1.activeClickableMenu = null;
                    Monitor.Log($"[agent_gn] Sleep confirmed via answerDialogueAction (tick {_goBedTicks})", LogLevel.Info);
                    _goBedPhase = GoBedPhase.Inactive;
                    return;
                }
            }
            catch { }

            // ── 级别 2：检测 DialogueBox 并点击 Yes ──
            if (Game1.activeClickableMenu is StardewValley.Menus.DialogueBox dlg)
            {
                try
                {
                    if (dlg.responses != null && dlg.responses.Length > 0)
                    {
                        var key = dlg.responses[0].responseKey;
                        Game1.activeClickableMenu = null;
                        Game1.currentLocation?.answerDialogueAction(key, null);
                        Monitor.Log($"[agent_gn] Sleep confirmed via DialogueBox response '{key}' (tick {_goBedTicks})", LogLevel.Info);
                        _goBedPhase = GoBedPhase.Inactive;
                        return;
                    }
                    else
                    {
                        dlg.receiveLeftClick(0, 0); // 非问题对话框，点掉
                    }
                }
                catch { }
            }

            // ── 级别 3（tick 5）：设 isInBed + 重试 answerDialogueAction（V1 兜底时机）──
            if (_goBedTicks == 5)
            {
                Game1.player!.isInBed.Value = true;
                Game1.currentLocation?.answerDialogueAction("Sleep_Yes", null);
                Monitor.Log("[agent_gn] Tick 5: set isInBed + retry Sleep_Yes", LogLevel.Debug);
            }

            // ── 级别 4（tick 10）：重试 checkForAction ──
            if (_goBedTicks == 10 && _goBedRef != null)
            {
                Game1.player!.isInBed.Value = true;
                _goBedRef.checkForAction(Game1.player);
                Monitor.Log("[agent_gn] Tick 10: retry checkForAction with isInBed", LogLevel.Warn);
            }

            // ── 级别 5（tick 30）：Game1.NewDay 核弹 ──
            if (_goBedTicks >= 30)
            {
                Monitor.Log("[agent_gn] Tick 30: all methods failed, calling Game1.NewDay(0f)", LogLevel.Warn);
                try
                {
                    Game1.player!.isInBed.Value = true;
                    if (Game1.activeClickableMenu != null)
                        Game1.activeClickableMenu = null;
                    Game1.NewDay(0f);
                }
                catch (Exception ex)
                {
                    Monitor.Log($"[agent_gn] Game1.NewDay failed: {ex.Message}", LogLevel.Error);
                }
                _goBedPhase = GoBedPhase.Inactive;
            }
        }

        /*────────────────────────────────────────────
         *  HUD 指示器上下文
         *────────────────────────────────────────────*/
        private HudOverlay.IndicatorContext BuildIndicatorContext()
        {
            string? targetName = null;
            if (Context.IsWorldReady && Game1.player != null)
            {
                // 找到 AI 控制的目标玩家名
                if (string.IsNullOrEmpty(_config.TargetPlayerId))
                {
                    targetName = Game1.player.Name;
                }
                else if (ulong.TryParse(_config.TargetPlayerId, out var targetId))
                {
                    foreach (var farmer in Game1.getOnlineFarmers())
                    {
                        if ((ulong)farmer.UniqueMultiplayerID == targetId)
                        {
                            targetName = farmer.Name;
                            break;
                        }
                    }
                    targetName ??= _config.TargetPlayerId; // fallback 显示 ID
                }
            }

            return new HudOverlay.IndicatorContext
            {
                HasToken = !string.IsNullOrWhiteSpace(_config.PlatformToken),
                EnableAIControl = _config.EnableAIControl,
                IsAIControlledInstance = _isAIControlledInstance,
                TargetPlayerName = targetName,
            };
        }

        /*────────────────────────────────────────────
         *  AI 实例判定（/agent_control 设置后 + 存档加载时调用）
         *────────────────────────────────────────────*/
        private void RecalcIsAIControlled()
        {
            if (Game1.player == null)
            {
                _isAIControlledInstance = false;
                return;
            }

            ulong bridgePlayerId = (ulong)Game1.player.UniqueMultiplayerID;

            if (!string.IsNullOrEmpty(_config.TargetPlayerId)
                && ulong.TryParse(_config.TargetPlayerId, out var parsedId))
            {
                _isAIControlledInstance = bridgePlayerId == parsedId;
            }
            else
            {
                // 未通过 /agent_control 设置目标 → 没有 AI 实例
                _isAIControlledInstance = false;
            }

            if (_isAIControlledInstance)
                Monitor.Log($"[AI 实例] {Game1.player.Name}({bridgePlayerId}) — 接受命令执行", LogLevel.Info);
            else
                Monitor.Log($"[观察实例] {Game1.player.Name}({bridgePlayerId}) — 仅数据上报", LogLevel.Info);
        }

        /*────────────────────────────────────────────
         *  Bridge 热重载（/agent_reload + starcoplay_reload + 本地控制面共用入口）
         *────────────────────────────────────────────*/

        /// <summary>
        /// 重载核心 — 无聊天副作用，仅返回 <see cref="ReloadResult"/>。
        /// 供 HandleReload（聊天命令）、SMAPI 控制台命令、LocalReloadPortal 共用。
        /// 成功仅表示 config applied + connection attempt started，不表示已 ready/已连接。
        /// </summary>
        private ReloadResult RunReload()
        {
            var result = _reloadCoordinator.TryReload(
                readConfig: () => Helper.ReadConfig<ModConfig>(),
                isBusyChecker: () =>
                {
                    bool cpBusy = _commandProcessor != null && _commandProcessor.IsBusyForProtocol;
                    bool pdBusy = _protoDispatcher != null && _protoDispatcher.IsBusy;
                    if (cpBusy)
                        return (true, $"CommandProcessor: {_commandProcessor!.ProtocolBusyReason}");
                    if (pdBusy)
                        return (true, "ProtoDispatcher");
                    return (false, "");
                },
                onConfigReloaded: OnConfigReloaded,
                connectionAttemptStarted: () => Context.IsWorldReady);

            // 所有入口共享相同的成功收尾，避免本地控制面成功后下一 tick
            // 又被 GMCM/多人消息遗留的 flag 触发第二次重建。
            if (result.IsSuccess)
                _needBridgeRebuild = false;
            return result;
        }

        /// <summary>
        /// 面向聊天/控制台的热重载入口。供 ChatCommandHandler（/agent_reload）和
        /// SMAPI 控制台（starcoplay_reload）共用。返回 null 表示成功，否则为错误描述。
        /// </summary>
        private string? HandleReload()
        {
            var result = RunReload();

            if (result.IsSuccess)
            {
                // 聊天框通知（仅当游戏已加载；未加载存档不宣称连接）
                if (Context.IsWorldReady && Game1.chatBox != null)
                {
                    var mode = _bridge is LocalDirectBridge ? "LocalDirect" : "WebSocket";
                    var state = result.ConnectionAttemptStarted
                        ? "连接尝试已启动"
                        : "将在存档加载后建立连接";
                    Game1.chatBox.addInfoMessage($"[Star CoPlay] 配置已应用（{mode}），{state}");
                }
                return null;
            }

            // 失败保留 _needBridgeRebuild 原值，不修改

            var detail = result.Detail ?? "";
            var msg = result.Outcome switch
            {
                ReloadResult.Phase.RefusedSingleFlight => "重载正在进行中，请稍后再试",
                ReloadResult.Phase.RefusedBusy => $"当前有命令执行中，请等待完成后再重载（{detail}）",
                ReloadResult.Phase.ConfigReadFailed => $"配置文件读取失败 [{detail}]",
                ReloadResult.Phase.BridgeRebuildFailed => $"Bridge 重建失败 [{detail}]",
                _ => $"未知错误: {result.Outcome}"
            };

            if (Context.IsWorldReady && Game1.chatBox != null)
                Game1.chatBox.addInfoMessage($"[Star CoPlay] {msg}");

            return msg;
        }

        /// <summary>
        /// Bridge 重建回调 — 由 ConfigReloadCoordinator 在配置读取成功后调用。
        ///
        /// 执行顺序：
        ///   1. 保存旧引用 + 旧 config（用于失败回滚）
        ///   2. 构造新 bridge 及依赖（不 Initialize）
        ///   3. Shutdown 旧 bridge
        ///   4. 原子换运行态引用（commit 边界）
        ///   5. Initialize 新 bridge
        ///
        /// 若 commit 边界后（步骤 4-5）失败：
        ///   - 恢复 oldConfig 运行态；不覆盖用户已编辑的 config.json
        ///   - 从 oldConfig 构造全新的 rollback bridge（绝不复用已 Shutdown 的旧 bridge）
        ///   - rollback 构造失败时记录稳定错误码（不泄露 secret），放弃恢复
        /// </summary>
        private void OnConfigReloaded(ModConfig newConfig)
        {
            // 保存旧状态（用于失败回滚）
            var oldConfig = _config;
            var oldBridge = _bridge;
            var oldWsBridgeRef = _wsBridgeRef;
            var oldProtocolBridgeRef = _protocolBridgeRef;
            var oldCommandProcessor = _commandProcessor;
            var oldEventEmitter = _eventEmitter;
            var oldStateWriter = _stateWriter;
            var oldProtoScheduler = _protoScheduler;
            var oldProtoDispatcher = _protoDispatcher;
            var oldAppearanceSync = _appearanceSync;
            var wasActive = _bridgeActive;

            // 应用自动迁移（与 ApplyConfigMigrations 一致）
            if (!newConfig.UseInputBridgeHandlers && !string.IsNullOrWhiteSpace(newConfig.PlatformToken))
            {
                newConfig.UseInputBridgeHandlers = true;
                Monitor.Log("[Config] 已自动启用 UseInputBridgeHandlers（检测到平台 Token）", LogLevel.Info);
            }

            var playerUniqueId = Context.IsWorldReady && Game1.player != null
                ? (ulong?)Game1.player.UniqueMultiplayerID : null;

            // ── Phase 1: 构造新 bridge + 依赖（不 Initialize，无副作用）──
            IBridge newBridge;
            WsBridge? newWsBridgeRef;
            IProtocolBridge? newProtocolBridgeRef;
            EventEmitter newEventEmitter;
            GameStateWriter newStateWriter;
            CommandProcessor newCommandProcessor;
            ProtoCommandScheduler newProtoScheduler;
            ProtoCommandDispatcher newProtoDispatcher;
            AppearanceSync newAppearanceSync;

            {
                // 临时切换 _config 供 CreateBridge 读取（不持久化）
                var savedConfig = _config;
                _config = newConfig;
                try
                {
                    newBridge = CreateBridge(Helper.DirectoryPath, playerUniqueId);
                    newBridge.SetLogger(_logger);
                    newWsBridgeRef = _wsBridgeRef; // CreateBridge 会设置 _wsBridgeRef
                    newProtocolBridgeRef = _protocolBridgeRef;

                    newEventEmitter = new EventEmitter(Monitor, _logger, newBridge, newConfig);
                    newStateWriter = new GameStateWriter(Monitor, newBridge, _state, _actions);
                    newCommandProcessor = new CommandProcessor(Monitor, _logger, newBridge, newEventEmitter,
                        _actions, _state, _adapterV2, _moveToHandlerForProcessor, _animAwaiter,
                        _compoundDispatcher, playerUniqueId ?? 0, IsDevMode);

                    newProtoScheduler = new ProtoCommandScheduler(
                        _protoRegistry, _protoResultFactory, Monitor,
                        () => newCommandProcessor.IsBusyForProtocol,
                        () => newCommandProcessor.ProtocolBusyReason);
                    newProtoDispatcher = new ProtoCommandDispatcher(
                        _protoRegistry, newProtoScheduler, _protoResultFactory, Monitor);

                    newAppearanceSync = new AppearanceSync(Monitor, _logger, () => newBridge as WsBridge);
                }
                finally
                {
                    _config = savedConfig;
                    // 临时恢复 _wsBridgeRef / _protocolBridgeRef 以免状态不一致
                    _wsBridgeRef = oldWsBridgeRef;
                    _protocolBridgeRef = oldProtocolBridgeRef;
                }
            }

            // ── Phase 2: Shutdown 旧 bridge（新 bridge 尚未 Initialize，无并行）──
            _bridgeActive = false;
            if (oldWsBridgeRef != null)
            {
                oldWsBridgeRef.ConnectionStateChanged -= OnWsConnectionStateChanged;
                oldWsBridgeRef.HostedStatusReceived -= OnWsHostedStatusReceived;
            }
            if (wasActive && oldBridge != null)
            {
                try { oldBridge.Shutdown(); }
                catch (Exception ex)
                {
                    Monitor.Log($"[Reload] old bridge shutdown error (non-fatal) [{ex.GetType().Name}]", LogLevel.Warn);
                }
            }

            // ── Phase 3: Commit — 原子换运行态引用 ──
            _config = newConfig;
            _bridge = newBridge;
            _wsBridgeRef = newWsBridgeRef;
            _protocolBridgeRef = newProtocolBridgeRef;
            _eventEmitter = newEventEmitter;
            _stateWriter = newStateWriter;
            _commandProcessor = newCommandProcessor;
            _protoScheduler = newProtoScheduler;
            _protoDispatcher = newProtoDispatcher;
            _appearanceSync = newAppearanceSync;
            Interlocked.Exchange(ref _pendingAppearanceSync, 0);

            // ── Phase 4: Initialize 新 bridge（commit 边界后）──
            try
            {
                if (Context.IsWorldReady)
                {
                    RecalcIsAIControlled();
                    newBridge.Initialize();
                    _bridgeActive = true;
                    newBridge.WriteAdapterInfo(_adapterV2.GenerateAdapterInfoJson());
                    Monitor.Log("[Reload] bridge initialized for current save", LogLevel.Info);
                }
                else
                {
                    Monitor.Log("[Reload] config reloaded (no save loaded, bridge not initialized)", LogLevel.Info);
                }

                // config.json 已由用户修改；这里只持久化成功激活后发生的兼容迁移。
                // 写盘失败不撤销已成功启动的连接尝试，也不输出异常原文。
                try { Helper.WriteConfig(newConfig); }
                catch (Exception writeEx)
                {
                    Monitor.Log($"[Reload] migrated config persistence failed [{writeEx.GetType().Name}]", LogLevel.Warn);
                }
            }
            catch (Exception ex)
            {
                // commit 边界后 Initialize 失败 → 必须从 oldConfig 构造全新 rollback bridge
                Monitor.Log($"[Reload] bridge init failed after commit, rolling back [{ex.GetType().Name}]", LogLevel.Error);

                // 恢复旧运行配置。config.json 是用户的新输入，失败时不得用旧值覆盖。
                _config = oldConfig;
                if (newWsBridgeRef != null)
                {
                    newWsBridgeRef.ConnectionStateChanged -= OnWsConnectionStateChanged;
                    newWsBridgeRef.HostedStatusReceived -= OnWsHostedStatusReceived;
                }
                try { newBridge.Shutdown(); }
                catch (Exception stopEx)
                {
                    Monitor.Log($"[Reload] failed bridge shutdown error [{stopEx.GetType().Name}]", LogLevel.Warn);
                }

                // 从 oldConfig 构造全新的 rollback bridge（绝不复用已 Shutdown 的旧 bridge）
                // _config 已在上面恢复为 oldConfig，CreateBridge 读取 _config 确定 WS URL 等
                try
                {
                    var rollbackBridge = CreateBridge(Helper.DirectoryPath, playerUniqueId);
                    rollbackBridge.SetLogger(_logger);
                    var rbWsBridgeRef = _wsBridgeRef;
                    var rbProtocolBridgeRef = _protocolBridgeRef;

                    var rbEventEmitter = new EventEmitter(Monitor, _logger, rollbackBridge, oldConfig);
                    var rbStateWriter = new GameStateWriter(Monitor, rollbackBridge, _state, _actions);
                    var rbCommandProcessor = new CommandProcessor(Monitor, _logger, rollbackBridge, rbEventEmitter,
                        _actions, _state, _adapterV2, _moveToHandlerForProcessor, _animAwaiter,
                        _compoundDispatcher, playerUniqueId ?? 0, IsDevMode);

                    var rbProtoScheduler = new ProtoCommandScheduler(
                        _protoRegistry, _protoResultFactory, Monitor,
                        () => rbCommandProcessor.IsBusyForProtocol,
                        () => rbCommandProcessor.ProtocolBusyReason);
                    var rbProtoDispatcher = new ProtoCommandDispatcher(
                        _protoRegistry, rbProtoScheduler, _protoResultFactory, Monitor);

                    var rbAppearanceSync = new AppearanceSync(Monitor, _logger, () => rollbackBridge as WsBridge);

                    _bridge = rollbackBridge;
                    _wsBridgeRef = rbWsBridgeRef;
                    _protocolBridgeRef = rbProtocolBridgeRef;
                    _eventEmitter = rbEventEmitter;
                    _stateWriter = rbStateWriter;
                    _commandProcessor = rbCommandProcessor;
                    _protoScheduler = rbProtoScheduler;
                    _protoDispatcher = rbProtoDispatcher;
                    _appearanceSync = rbAppearanceSync;
                    Interlocked.Exchange(ref _pendingAppearanceSync, 0);

                    if (Context.IsWorldReady)
                    {
                        RecalcIsAIControlled();
                        rollbackBridge.Initialize();
                        _bridgeActive = true;
                        rollbackBridge.WriteAdapterInfo(_adapterV2.GenerateAdapterInfoJson());
                    }
                    Monitor.Log("[Reload] rollback bridge constructed and initialized", LogLevel.Info);
                }
                catch (Exception rbEx)
                {
                    _bridgeActive = false;
                    Monitor.Log($"[Reload] rollback bridge construction failed [{rbEx.GetType().Name}]", LogLevel.Error);
                }

                throw; // 重新抛出，让 coordinator 记录 BridgeRebuildFailed
            }
        }

        /*────────────────────────────────────────────
         *  Bridge 热重载（GMCM 保存后触发）
         *────────────────────────────────────────────*/
        private void RebuildBridge()
        {
            Monitor.Log("GMCM 配置变更，重建 Bridge...", LogLevel.Info);
            if (_bridgeActive)
            {
                _bridge.Shutdown();
                _bridgeActive = false;
            }
            // 解除旧事件订阅
            if (_wsBridgeRef != null)
            {
                _wsBridgeRef.ConnectionStateChanged -= OnWsConnectionStateChanged;
                _wsBridgeRef.HostedStatusReceived -= OnWsHostedStatusReceived;
            }

            var playerUniqueId = Context.IsWorldReady && Game1.player != null
                ? (ulong?)Game1.player.UniqueMultiplayerID : null;
            _bridge = CreateBridge(Helper.DirectoryPath, playerUniqueId);
            _bridge.SetLogger(_logger);
            _eventEmitter = new EventEmitter(Monitor, _logger, _bridge, _config);

            _stateWriter = new GameStateWriter(Monitor, _bridge, _state, _actions);
            _commandProcessor = new CommandProcessor(Monitor, _logger, _bridge, _eventEmitter,
                _actions, _state, _adapterV2, _moveToHandlerForProcessor, _animAwaiter, _compoundDispatcher,
                playerUniqueId ?? 0, IsDevMode);
            _appearanceSync = new AppearanceSync(Monitor, _logger, () => _bridge as WsBridge);
            Interlocked.Exchange(ref _pendingAppearanceSync, 0);

            // Proto-native scheduler/dispatcher 重建
            _protoScheduler = new ProtoCommandScheduler(
                _protoRegistry,
                _protoResultFactory,
                Monitor,
                () => _commandProcessor.IsBusyForProtocol,
                () => _commandProcessor.ProtocolBusyReason);
            _protoDispatcher = new ProtoCommandDispatcher(
                _protoRegistry,
                _protoScheduler,
                _protoResultFactory,
                Monitor);

            if (Context.IsWorldReady)
            {
                RecalcIsAIControlled();
                _bridge.Initialize();
                _bridgeActive = true;
                _bridge.WriteAdapterInfo(_adapterV2.GenerateAdapterInfoJson());
            }
            Monitor.Log("Bridge 已热重载", LogLevel.Info);
        }

        /*────────────────────────────────────────────
         *  TEMP: test_input — L0 InputBridge 端到端测试
         *  用于验证 InputBridge / InputCombo / MenuNavigator 的 4 种交互模式。
         *  action 列表：face / tap / hold / hold_for / use_tool / interact /
         *               equip / menu_click / status
         *  验证完成后整段（HandleTestInputCommand + TestInput* 辅助方法）应删除。
         *────────────────────────────────────────────*/
        /*────────────────────────────────────────────
         *  [已弃用] test_input — L0 端到端测试临时命令
         *
         *  模拟输入执行原子已完整实现并验证（face/interact/use_tool/equip/menu/move_to 三模式）。
         *  test_input 仅在阶段一开发期用于绕过 L1 直接测试 InputBridge / InputCombo。
         *
         *  正式 L1 命令（face/interact/use_tool/equip/move_to/open_menu/menu_click/menu_close）
         *  在 ModConfig.UseInputBridgeHandlers=true 时全部可用，请优先使用。
         *
         *  本 handler + 4 个 TestInput* 辅助方法计划在确认模拟输入执行原子稳定后整段删除。
         *────────────────────────────────────────────*/
        private V2Result? HandleTestInputCommand(V2Command cmd)
        {
            // TEMP: L0 端到端测试命令，验证完成后应删除
            if (!TestInputTryGetString(cmd.Params, "action", out var action))
            {
                return ResultBuilder.Failure(
                    cmd.Seq, cmd.ActionId,
                    ResultBuilder.Errors.InvalidParams,
                    "test_input 缺少 action 参数");
            }

            try
            {
                switch (action)
                {
                    // ── 同步返回：状态查询 ──
                    case "status":
                    {
                        return ResultBuilder.Success(
                                cmd.Seq, cmd.ActionId,
                                new
                                {
                                    is_busy = _inputBridge.IsBusy,
                                    is_player_free = _inputBridge.IsPlayerFree,
                                })
                            .WithObservation($"InputBridge busy={_inputBridge.IsBusy} player_free={_inputBridge.IsPlayerFree}");
                    }

                    // ── 异步入队：InputCombo.Face（方向键面朝） ──
                    case "face":
                    {
                        if (!TestInputTryGetInt(cmd.Params, "direction", out var direction)
                            || direction < 0 || direction > 3)
                        {
                            return ResultBuilder.Failure(
                                cmd.Seq, cmd.ActionId,
                                ResultBuilder.Errors.InvalidParams,
                                "face 需要 direction 参数 (0=上 1=右 2=下 3=左)");
                        }
                        _inputCombo.Face(direction);
                        return ResultBuilder.Success(
                                cmd.Seq, cmd.ActionId,
                                new { action = "face", direction, queued = true })
                            .WithObservation($"已入队 face direction={direction}");
                    }

                    // ── 异步入队：InputBridge.Tap ──
                    case "tap":
                    {
                        if (!TestInputTryGetButton(cmd.Params, "button", out var btn, out var btnErr))
                            return ResultBuilder.Failure(cmd.Seq, cmd.ActionId, ResultBuilder.Errors.InvalidParams, btnErr);

                        _inputBridge.Tap(btn);
                        return ResultBuilder.Success(
                                cmd.Seq, cmd.ActionId,
                                new { action = "tap", button = btn.ToString(), queued = true })
                            .WithObservation($"已入队 tap button={btn}");
                    }

                    // ── 异步入队：InputBridge.Hold（带停止条件，固定 ticks 后释放） ──
                    case "hold":
                    {
                        if (!TestInputTryGetButton(cmd.Params, "button", out var btn, out var btnErr))
                            return ResultBuilder.Failure(cmd.Seq, cmd.ActionId, ResultBuilder.Errors.InvalidParams, btnErr);

                        if (!TestInputTryGetInt(cmd.Params, "ticks", out var ticks) || ticks <= 0)
                            return ResultBuilder.Failure(cmd.Seq, cmd.ActionId, ResultBuilder.Errors.InvalidParams, "hold 需要正整数 ticks");

                        // hold 模式以 stopCondition 触发释放；测试用：用帧数计数器作为停止条件
                        int startFrame = Game1.ticks;
                        int targetFrame = startFrame + ticks;
                        _inputBridge.Hold(btn,
                            stopCondition: () => Game1.ticks >= targetFrame,
                            timeout: ticks + 60);

                        return ResultBuilder.Success(
                                cmd.Seq, cmd.ActionId,
                                new { action = "hold", button = btn.ToString(), ticks, queued = true })
                            .WithObservation($"已入队 hold button={btn} for {ticks} ticks");
                    }

                    // ── 异步入队：InputBridge.HoldFor（固定帧数按住） ──
                    case "hold_for":
                    {
                        if (!TestInputTryGetButton(cmd.Params, "button", out var btn, out var btnErr))
                            return ResultBuilder.Failure(cmd.Seq, cmd.ActionId, ResultBuilder.Errors.InvalidParams, btnErr);

                        if (!TestInputTryGetInt(cmd.Params, "ticks", out var ticks) || ticks <= 0)
                            return ResultBuilder.Failure(cmd.Seq, cmd.ActionId, ResultBuilder.Errors.InvalidParams, "hold_for 需要正整数 ticks");

                        _inputBridge.HoldFor(btn, ticks);
                        return ResultBuilder.Success(
                                cmd.Seq, cmd.ActionId,
                                new { action = "hold_for", button = btn.ToString(), ticks, queued = true })
                            .WithObservation($"已入队 hold_for button={btn} ticks={ticks}");
                    }

                    // ── 异步入队：InputCombo.UseTool ──
                    case "use_tool":
                    {
                        if (!TestInputTryGetInt(cmd.Params, "x", out var x)
                            || !TestInputTryGetInt(cmd.Params, "y", out var y))
                        {
                            return ResultBuilder.Failure(
                                cmd.Seq, cmd.ActionId,
                                ResultBuilder.Errors.InvalidParams,
                                "use_tool 需要 x, y 参数");
                        }
                        _inputCombo.UseTool(new Vector2(x, y));
                        return ResultBuilder.Success(
                                cmd.Seq, cmd.ActionId,
                                new { action = "use_tool", x, y, queued = true })
                            .WithObservation($"已入队 use_tool target=({x},{y})");
                    }

                    // ── 异步入队：InputCombo.Interact ──
                    case "interact":
                    {
                        if (!TestInputTryGetInt(cmd.Params, "x", out var x)
                            || !TestInputTryGetInt(cmd.Params, "y", out var y))
                        {
                            return ResultBuilder.Failure(
                                cmd.Seq, cmd.ActionId,
                                ResultBuilder.Errors.InvalidParams,
                                "interact 需要 x, y 参数");
                        }
                        _inputCombo.Interact(new Vector2(x, y));
                        return ResultBuilder.Success(
                                cmd.Seq, cmd.ActionId,
                                new { action = "interact", x, y, queued = true })
                            .WithObservation($"已入队 interact target=({x},{y})");
                    }

                    // ── 异步入队：InputCombo.EquipSlot ──
                    case "equip":
                    {
                        if (!TestInputTryGetInt(cmd.Params, "slot", out var slot)
                            || slot < 1 || slot > 12)
                        {
                            return ResultBuilder.Failure(
                                cmd.Seq, cmd.ActionId,
                                ResultBuilder.Errors.InvalidParams,
                                "equip 需要 slot 参数 (1-12)");
                        }
                        _inputCombo.EquipSlot(slot);
                        return ResultBuilder.Success(
                                cmd.Seq, cmd.ActionId,
                                new { action = "equip", slot, queued = true })
                            .WithObservation($"已入队 equip slot={slot}");
                    }

                    // ── 异步入队：MenuNavigator.Click（DPad 导航 + A 键确认） ──
                    case "menu_click":
                    {
                        if (!TestInputTryGetInt(cmd.Params, "component_id", out var componentId))
                        {
                            return ResultBuilder.Failure(
                                cmd.Seq, cmd.ActionId,
                                ResultBuilder.Errors.InvalidParams,
                                "menu_click 需要 component_id 参数");
                        }
                        bool ok = _menuNavigator.Click(componentId);
                        if (!ok)
                        {
                            return ResultBuilder.Failure(
                                cmd.Seq, cmd.ActionId,
                                ResultBuilder.Errors.NotSupported,
                                $"menu_click 失败：无菜单或组件 {componentId} 不可达");
                        }
                        return ResultBuilder.Success(
                                cmd.Seq, cmd.ActionId,
                                new { action = "menu_click", component_id = componentId, queued = true })
                            .WithObservation($"已入队 menu_click component_id={componentId}");
                    }

                    default:
                        return ResultBuilder.Failure(
                            cmd.Seq, cmd.ActionId,
                            ResultBuilder.Errors.InvalidParams,
                            $"未知 action: {action}（可选: face/tap/hold/hold_for/use_tool/interact/equip/menu_click/status）");
                }
            }
            catch (Exception ex)
            {
                Monitor.Log($"[test_input] 异常 action={action}: {ex.Message}\n{ex.StackTrace}", LogLevel.Error);
                return ResultBuilder.Failure(
                    cmd.Seq, cmd.ActionId,
                    ResultBuilder.Errors.ExecutionFailed,
                    $"test_input 执行异常: {ex.Message}");
            }
        }

        // TEMP: test_input 辅助方法 — 验证完成后应一并删除
        private static bool TestInputTryGetString(Dictionary<string, object> p, string key, out string value)
        {
            value = "";
            if (!p.TryGetValue(key, out var raw)) return false;
            if (raw is JsonElement je)
            {
                if (je.ValueKind == JsonValueKind.String)
                {
                    value = je.GetString() ?? "";
                    return !string.IsNullOrEmpty(value);
                }
                value = je.ToString();
                return !string.IsNullOrEmpty(value);
            }
            value = raw?.ToString() ?? "";
            return !string.IsNullOrEmpty(value);
        }

        private static bool TestInputTryGetInt(Dictionary<string, object> p, string key, out int value)
        {
            value = 0;
            if (!p.TryGetValue(key, out var raw)) return false;
            switch (raw)
            {
                case int i: value = i; return true;
                case long l when l >= int.MinValue && l <= int.MaxValue: value = (int)l; return true;
                case double d when d >= int.MinValue && d <= int.MaxValue: value = (int)d; return true;
                case string s: return int.TryParse(s, System.Globalization.NumberStyles.Integer,
                    System.Globalization.CultureInfo.InvariantCulture, out value);
                case JsonElement e when e.ValueKind == JsonValueKind.Number: return e.TryGetInt32(out value);
                case JsonElement e when e.ValueKind == JsonValueKind.String:
                    return int.TryParse(e.GetString(), System.Globalization.NumberStyles.Integer,
                        System.Globalization.CultureInfo.InvariantCulture, out value);
                default: return false;
            }
        }

        private static bool TestInputTryGetButton(Dictionary<string, object> p, string key, out SButton btn, out string error)
        {
            btn = SButton.None;
            error = "";
            if (!TestInputTryGetString(p, key, out var s))
            {
                error = $"缺少 {key} 参数";
                return false;
            }
            if (!Enum.TryParse<SButton>(s, ignoreCase: true, out btn))
            {
                error = $"未知 SButton 名称: {s}";
                return false;
            }
            return true;
        }

        /*────────────────────────────────────────────
         *  存档加载 — 打印初始状态 + 启动报告会话
         *────────────────────────────────────────────*/
        private void OnSaveLoaded(object? sender, SaveLoadedEventArgs e)
        {
            _reporter.StartSession();

            // 注入 ModConfig 中的 WarpDirection 覆盖（PressDirection 三级查找链最高优先）
            MapData.RegisterDirectionOverrides(_config?.WarpDirectionOverrides);

            // 从游戏运行时数据动态构建地图连通表
            var exitCount = MapData.BuildFromGameData(Monitor);

            // 重新初始化 BridgeManager — 每个实例绑定自己的玩家 bridge 目录
            ulong bridgePlayerId = (ulong)Game1.player.UniqueMultiplayerID;

            // 判断当前实例是否为 AI 控制的角色
            // TargetPlayerId 由 /agent_control <玩家> 设置，匹配时本实例为 AI 实例
            // 未设置（null/空）→ 没有 AI 实例，所有实例均为观察实例（避免多实例同时执行命令）
            RecalcIsAIControlled();

            // 清理旧 WsBridge 事件订阅，避免泄漏
            if (_wsBridgeRef != null)
            {
                _wsBridgeRef.ConnectionStateChanged -= OnWsConnectionStateChanged;
                _wsBridgeRef.HostedStatusReceived -= OnWsHostedStatusReceived;
            }

            _bridge = CreateBridge(Helper.DirectoryPath, bridgePlayerId);
            _bridge.SetLogger(_logger);
            _eventEmitter = new EventEmitter(Monitor, _logger, _bridge, _config);

            _stateWriter = new GameStateWriter(Monitor, _bridge, _state, _actions);
            _commandProcessor = new CommandProcessor(Monitor, _logger, _bridge, _eventEmitter, _actions, _state, _adapterV2, _moveToHandlerForProcessor, _animAwaiter, _compoundDispatcher, bridgePlayerId, IsDevMode);

            // Proto-native scheduler/dispatcher（依赖 CommandProcessor 的 busy 判断）
            _protoScheduler = new ProtoCommandScheduler(
                _protoRegistry,
                _protoResultFactory,
                Monitor,
                () => _commandProcessor.IsBusyForProtocol,
                () => _commandProcessor.ProtocolBusyReason);
            _protoDispatcher = new ProtoCommandDispatcher(
                _protoRegistry,
                _protoScheduler,
                _protoResultFactory,
                Monitor);

            // 外观只在 WS/Agent 链接建立后同步，由 OnUpdateTicked 在主线程执行。
            _appearanceSync = new AppearanceSync(Monitor, _logger, () => _bridge as WsBridge);
            Interlocked.Exchange(ref _pendingAppearanceSync, 0);
            _bridge.Initialize();

            // 动态生成 adapter_info.json — 基于实际注册的能力，确保与运行时一致
            var adapterInfoJson = _adapterV2.GenerateAdapterInfoJson();
            _bridge.WriteAdapterInfo(adapterInfoJson);
            Monitor.Log($"adapter_info.json 已动态生成，包含 {_adapterV2.RegisteredCapabilities.Count()} 个能力", LogLevel.Debug);

            _bridgeActive = true;
            _lastEnergyLowValue = float.MaxValue;

            // InputBridge: 启用 gamepad 模式以便通过虚拟输入操作菜单
            Game1.options.gamepadControls = true;

            // L0: 安装 IInputSimulator —— 失焦也能驱动玩家移动 / 交互
            // SDV 在玩家按 ESC 时会自动卸载 inputSimulator；InputBridge.OnUpdateTicking 每帧检测并自动重 install
            _inputBridge.Simulator.Install();

            if (string.IsNullOrWhiteSpace(_config.PlatformToken))
            {
                Interlocked.Exchange(ref _pendingChatMessage,
                    "请打开设置菜单 (GMCM) 粘贴平台 Token 以唤醒搭子 | star-coplay.luosea.cn");
                Monitor.Log("PlatformToken 未配置；Bridge 不会建立 WS 连接", LogLevel.Warn);
            }

            Monitor.Log("=== 存档已加载，初始状态 ===", LogLevel.Debug);
            Monitor.Log($"Bridge 目录: {_bridge.BridgeDir}", LogLevel.Info);
            Monitor.Log($"AI 控制状态: {(_config.EnableAIControl ? "已启用" : "已禁用")}", LogLevel.Info);
            LogWorldState();
            LogPlayerState();
            Monitor.Log("提示: 按 F7 切换 HUD 面板 | 输入 /agent_status 查看完整状态 | 输入 /agent_control 管理 AI 控制", LogLevel.Debug);

            // 重新注册 GMCM — 此时在线玩家列表可用，下拉框可以列出所有玩家
            GmcmIntegration.Register(
                Helper, ModManifest,
                getConfig: () => _config,
                saveConfig: () => Helper.WriteConfig(_config),
                onConfigChanged: () => _needBridgeRebuild = true,
                getWsBridge: () => _wsBridgeRef
            );
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
            _logger.LogStateChange($"=== 第 {Game1.dayOfMonth} 天开始 ({Game1.currentSeason} Y{Game1.year}) ===", LogLevel.Info);

            // 重置卡住检测和恢复状态
            _stuckDetector.Reset();
            _stuckRecovery.Reset();
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
         *  UpdateTicking — InputBridge 输入注入（在 Game1.Update 之前）
         *────────────────────────────────────────────*/
        private void OnUpdateTicking_InputBridge(object? sender, UpdateTickingEventArgs e)
        {
            if (_bridgeActive && _config.EnableAIControl && _isAIControlledInstance)
                _inputBridge.OnUpdateTicking();
        }

        /*────────────────────────────────────────────
         *  每 Tick — 高频执行循环（~60fps）
         *────────────────────────────────────────────*/
        private void OnUpdateTicked(object? sender, UpdateTickedEventArgs e)
        {
            // ── LocalReloadPortal 轮询（~1 Hz，独立于存档加载状态）──
            _portalTickCounter++;
            if (_portalTickCounter >= 60 && _localPortal != null)
            {
                _portalTickCounter = 0;
                var request = _localPortal.TryClaimRequest();
                if (request != null)
                {
                    var result = RunReload();
                    _localPortal.WriteResult(request.request_id, result);
                }
            }

            if (!Context.IsWorldReady) return;

            if (_bridge is LocalDirectBridge localControlBridge)
            {
                while (localControlBridge.TryTakeControl(out var controlSnapshot))
                    ApplyControlSnapshot(controlSnapshot);
                if (e.IsMultipleOf(60) && Game1.player != null)
                {
                    localControlBridge.WriteIdentity(
                        (ulong)Game1.player.UniqueMultiplayerID,
                        Game1.player.Name,
                        _isAIControlledInstance,
                        _config.TargetPlayerId,
                        _config.AgentControlRevision);
                }
            }

            if (Interlocked.Exchange(ref _pendingHostedControlRebase, 0) == 1)
                RebaseStaleHostedControllerClaim();

            var localRendezvousAvailable = LocalDirectBridge.ShouldSelectForMode(
                _config.BridgeMode,
                LocalDirectBridge.IsSelectableForThisInstance());
            var currentLocalBridge = _bridge as LocalDirectBridge;
            if (LocalDirectBridge.ShouldRebuildForLocalRendezvous(
                currentLocalBridge != null,
                currentLocalBridge?.IsLive ?? false,
                localRendezvousAvailable))
                _needBridgeRebuild = true;

            // Bridge 热重载（GMCM 保存后触发）
            if (_needBridgeRebuild)
            {
                _needBridgeRebuild = false;
                RebuildBridge();
            }

            // /agent_gn 到家后触发睡觉
            TickGoBed();

            if (Interlocked.Exchange(ref _pendingAppearanceSync, 0) == 1)
            {
                _appearanceSync?.Sync(force: true);
            }

            // 聊天框通知消费（WsBridge 事件在后台线程触发，此处主线程安全发送）
            var chatMsg = Interlocked.Exchange(ref _pendingChatMessage, null);
            if (chatMsg != null && Game1.chatBox != null)
            {
                Game1.chatBox.addInfoMessage($"[Star CoPlay] {chatMsg}");
            }

            // 日志刷新（每帧批量写入文件日志）
            _logger.Flush();

            // Proto-native 执行基座：drain inbound envelopes + tick active command
            if (_bridgeActive && _config.EnableAIControl && _isAIControlledInstance && _protocolBridgeRef != null)
            {
                var envelopes = _protocolBridgeRef.ReadNewProtocolEnvelopes();
                foreach (var envelope in envelopes)
                {
                    var controlKind = ControlFramingCodec.Classify(envelope);
                    if (controlKind == ControlFrameKind.ProtoCancel)
                    {
                        var cancelResult = _protoDispatcher.Cancel(envelope);
                        if (cancelResult != null)
                            _protocolBridgeRef.WriteProtocolResult(cancelResult.Value.RequestId, cancelResult.Value.SessionId, cancelResult.Value.Result);
                    }
                    else if (controlKind == ControlFrameKind.ProtoStatus)
                    {
                        var status = _protoDispatcher.Status(envelope);
                        _protocolBridgeRef.WriteProtocolStatus(envelope.RequestId, envelope.SessionId, status);
                    }
                    else if (envelope.BodyCase == Envelope.BodyOneofCase.CommandRequest)
                    {
                        if (_protoRegistry.IsRegistered(envelope.CommandRequest.ParamsCase))
                        {
                            var dispatchResult = _protoDispatcher.Dispatch(envelope);
                            if (dispatchResult != null)
                                _protocolBridgeRef.WriteProtocolResult(dispatchResult.Value.RequestId, dispatchResult.Value.SessionId, dispatchResult.Value.Result);
                        }
                        else
                        {
                            if (_wsBridgeRef != null)
                                _wsBridgeRef.FallbackToLegacyMapper(envelope);
                        }
                    }
                }

                var tickResult = _protoDispatcher.Tick();
                if (tickResult != null)
                    _protocolBridgeRef.WriteProtocolResult(tickResult.Value.RequestId, tickResult.Value.SessionId, tickResult.Value.Result);
            }

            // Bridge: 每帧轮询命令（仅 AI 控制实例 + 启用时）。
            // Dev-only manual protocol replay can create pending V2 work on an observer
            // instance; keep ticking that pending work without opening bridge command intake.
            var canReadBridgeCommands = _bridgeActive && _config.EnableAIControl && _isAIControlledInstance;
            var canTickManualProtocolReplay = IsDevMode && _commandProcessor.HasPendingV2Command;
            if (canReadBridgeCommands || canTickManualProtocolReplay)
            {
                _commandProcessor.Poll(readBridgeCommands: canReadBridgeCommands);
            }

            // Bridge: 每 15 tick (~4次/秒) 检测对话框状态变化，推送事件
            if (_bridgeActive && e.IsMultipleOf(15))
            {
                DetectDialogueStateChange();
            }

            // 每秒一次更新卡住检测（仅 AI 实例 + Agent 有活跃动作时）
            if (_isAIControlledInstance && e.IsMultipleOf(60))
            {
                if (_actions.IsBusy)
                    _stuckDetector.Update(Game1.player.Tile);
                else
                    _stuckDetector.Reset();
            }

            // 驱动卡住恢复冷却计时（仅 AI 实例）
            if (_isAIControlledInstance)
                _stuckRecovery.Tick();

            // 如果不在卡住状态且玩家可以移动，通知恢复（仅 AI 实例）
            if (_isAIControlledInstance && !_stuckDetector.IsStuck && Game1.player.canMove)
                _stuckRecovery.OnRecovered();

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

            // 卡住检测 + 三级恢复（仅在 Agent 有活跃动作时，或玩家无法移动时触发）
            var isPlayerImmobile = !Game1.player.canMove && Game1.player.UsingTool;
            if (_stuckDetector.IsStuck && !_animAwaiter.IsWaiting && (_actions.IsBusy || isPlayerImmobile))
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
                _stuckRecovery.TryRecover();
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

            int minutes = e.NewTime % 100;

            // Bridge: 仅在整点和半点推送 time_changed 事件（降频：120次/天 → ~40次/天）
            if (_bridgeActive && (minutes == 0 || minutes == 30))
            {
                _eventEmitter.EmitTimeChanged(e.NewTime, FormatTime(e.NewTime));

                // 整点记录状态快照到文件日志
                if (minutes == 0)
                {
                    var player = Game1.player;
                    var location = Game1.currentLocation;
                    _logger.LogStateChange(
                        $"整点快照: 时间={FormatTime(e.NewTime)} 位置={location?.Name ?? "?"}({(int)player.Tile.X},{(int)player.Tile.Y}) " +
                        $"体力={player.stamina:F0}/{player.maxStamina.Value} 金钱={player.Money}g 工具={player.CurrentTool?.DisplayName ?? "无"}",
                        LogLevel.Info);
                }
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

            Monitor.Log($"地图切换: {e.OldLocation.NameOrUniqueName} → {e.NewLocation.NameOrUniqueName}", LogLevel.Debug);
            _logger.LogStateChange($"地图切换: {e.OldLocation.NameOrUniqueName} → {e.NewLocation.NameOrUniqueName}");
            _stuckDetector.Reset();
            _stuckRecovery.Reset();

            // Bridge: 推送 player_warped 事件
            if (_bridgeActive)
            {
                var playerTile = Game1.player.Tile;
                _eventEmitter.EmitPlayerWarped(e.OldLocation.NameOrUniqueName, e.NewLocation.NameOrUniqueName, (int)playerTile.X, (int)playerTile.Y);
            }
        }

        /*────────────────────────────────────────────
         *  背包变动
         *────────────────────────────────────────────*/
        private void OnInventoryChanged(object? sender, InventoryChangedEventArgs e)
        {
            if (!e.IsLocalPlayer) return;

            foreach (var item in e.Added)
            {
                Monitor.Log($"  [背包+] {item.DisplayName} x{item.Stack}", LogLevel.Debug);
                _logger.LogStateChange($"背包+: {item.DisplayName} x{item.Stack}");
            }
            foreach (var item in e.Removed)
            {
                Monitor.Log($"  [背包-] {item.DisplayName} x{item.Stack}", LogLevel.Debug);
                _logger.LogStateChange($"背包-: {item.DisplayName} x{item.Stack}");
            }

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
            // Returning to title ends this game instance. A later SaveLoaded
            // receives a fresh nonce and cannot resume the prior GameSession.
            _gameInstanceNonce = null;
            // Bridge: 关闭文件桥
            if (_bridgeActive)
            {
                _logger.LogBridge("Bridge 关闭: 退出存档", LogLevel.Info);
                // 退出存档是明确结束 GameSession，先通知 Gateway 释放 Controller。
                // ForceAbort 仅保留给进程退出等无法阻塞的异常场景。
                _wsBridgeRef?.EndGameSession();
                _bridge.Shutdown();
                _bridgeActive = false;
            }
            if (_wsBridgeRef != null)
            {
                _wsBridgeRef.ConnectionStateChanged -= OnWsConnectionStateChanged;
                _wsBridgeRef.HostedStatusReceived -= OnWsHostedStatusReceived;
                _wsBridgeRef = null;
            }

            var path = _reporter.GenerateReport();
            Monitor.Log($"退出报告已自动保存: {path}", LogLevel.Debug);
        }

        /*────────────────────────────────────────────
         *  游戏退出 — 确保 WsBridge 在 Steam 关闭网络层前彻底关闭
         *  防止 macOS 上 recursive_mutex crash
         *────────────────────────────────────────────*/
        private void OnProcessExit()
        {
            // ProcessExit 只有极短时间窗口，不能阻塞。
            // ForceAbort 只做 CTS.Cancel + ws.Abort()，不等待任何异步操作。
            try { _wsBridgeRef?.ForceAbort(); } catch { }
            _bridgeActive = false;
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

        /// <summary>
        /// 检测对话框/选项框状态变化，推送 dialogue_opened / dialogue_closed 事件。
        /// 去重逻辑：同一个对话框（相同文本）只推送一次 dialogue_opened。
        /// </summary>
        private void DetectDialogueStateChange()
        {
            try
            {
                var menu = Game1.activeClickableMenu;

                if (menu is DialogueBox dialogueBox)
                {
                    // 获取当前对话文本
                    string currentText;
                    try
                    {
                        currentText = dialogueBox.getCurrentString() ?? "";
                    }
                    catch
                    {
                        currentText = "";
                    }

                    // 去重：同一个对话框文本只推送一次
                    if (_dialogueActive && currentText == _lastDialogueText)
                        return;

                    _dialogueActive = true;
                    _lastDialogueText = currentText;

                    // 确定对话类型
                    string dialogueType;
                    List<Dictionary<string, object>>? choices = null;
                    string? npcName = null;

                    // 检测 NPC
                    try
                    {
                        if (dialogueBox.characterDialogue?.speaker != null)
                        {
                            npcName = dialogueBox.characterDialogue.speaker.Name;
                        }
                    }
                    catch
                    {
                        // ignore
                    }

                    if (dialogueBox.isQuestion && dialogueBox.responses != null && dialogueBox.responses.Length > 0)
                    {
                        dialogueType = "question";
                        choices = new List<Dictionary<string, object>>();
                        for (int i = 0; i < dialogueBox.responses.Length; i++)
                        {
                            var resp = dialogueBox.responses[i];
                            choices.Add(new Dictionary<string, object>
                            {
                                ["index"] = i,
                                ["text"] = resp.responseText ?? "",
                                ["key"] = resp.responseKey ?? ""
                            });
                        }
                    }
                    else if (npcName != null)
                    {
                        dialogueType = "dialogue";
                    }
                    else
                    {
                        dialogueType = "info";
                    }

                    _eventEmitter.EmitDialogueOpened(dialogueType, currentText, choices, npcName);
                    _logger.LogStateChange(
                        $"对话框打开: type={dialogueType} npc={npcName ?? "(none)"} choices={choices?.Count ?? 0} text={Truncate(currentText, 60)}",
                        LogLevel.Info);
                }
                else
                {
                    // 对话框关闭
                    if (_dialogueActive)
                    {
                        _dialogueActive = false;
                        _lastDialogueText = "";
                        _eventEmitter.EmitDialogueClosed();
                        _logger.LogStateChange("对话框关闭");
                    }
                }
            }
            catch (Exception ex)
            {
                Monitor.Log($"DetectDialogueStateChange 异常: {ex.Message}", LogLevel.Error);
            }
        }

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
                        _eventEmitter.EmitNpcNearby(npc.Name, location.NameOrUniqueName, (int)npc.Tile.X, (int)npc.Tile.Y, distance, hearts, talkedToday, giftedToday);
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
         *  多人消息处理 — AI 控制开关同步
         *────────────────────────────────────────────*/
        private void OnModMessageReceived(object? sender, ModMessageReceivedEventArgs e)
        {
            if (e.FromModID != Helper.ModRegistry.ModID || e.Type != "StarCoPlay.AIControl")
                return;

            AgentControlSnapshotMessage message;
            try { message = e.ReadAs<AgentControlSnapshotMessage>(); }
            catch (Exception ex)
            {
                Monitor.Log($"收到无效 AI 控制快照 [{ex.GetType().Name}]", LogLevel.Warn);
                return;
            }

            if (message.Action == "control_snapshot")
            {
                AgentControlSnapshot incoming;
                try { incoming = message.ToSnapshot(); }
                catch (Exception ex)
                {
                    Monitor.Log($"拒绝无效 AI 控制快照 [{ex.GetType().Name}]", LogLevel.Warn);
                    return;
                }
                ApplyControlSnapshot(incoming);
            }
            else if (message.Action == "go_bed")
            {
                // 只有 AI 控制的实例执行睡觉
                if (_isAIControlledInstance && _config.EnableAIControl)
                {
                    Monitor.Log("[agent_gn] 收到广播 go_bed，AI 实例开始执行", LogLevel.Info);
                    _actions.CancelCurrent();
                    TriggerGoBed();
                }
                else
                {
                    Monitor.Log($"[agent_gn] 收到广播 go_bed，非 AI 实例忽略 (isAI={_isAIControlledInstance})", LogLevel.Debug);
                }
            }
        }

        /// <summary>
        /// Apply one desired-control snapshot on the game thread. Shared by the
        /// multiplayer control message and the local_direct control frame.
        /// </summary>
        private void ApplyControlSnapshot(AgentControlSnapshot incoming)
        {
            var current = AgentControlSnapshot.FromConfig(_config);
            var decision = current.CompareIncoming(incoming);
            if (decision == AgentControlSnapshotDecision.Stale)
            {
                Monitor.Log($"忽略旧 AI 控制快照: incoming={incoming.Revision}, current={current.Revision}", LogLevel.Debug);
                return;
            }
            if (decision == AgentControlSnapshotDecision.Conflict)
            {
                Monitor.Log($"拒绝同 revision 异 payload 的 AI 控制快照: revision={incoming.Revision}", LogLevel.Warn);
                return;
            }
            if (decision == AgentControlSnapshotDecision.Idempotent)
                return;

            incoming.ApplyTo(_config);
            Helper.WriteConfig(_config);
            RecalcIsAIControlled();
            Monitor.Log($"接受 AI 控制快照: enabled={incoming.Enabled}, target={incoming.TargetPlayerId ?? "null"}, revision={incoming.Revision}, IsAI={_isAIControlledInstance}", LogLevel.Info);
            Game1.chatBox?.addInfoMessage(incoming.Enabled ? "AI 控制已启用" : "AI 控制已禁用");
            // The local_direct rendezvous is one-shot; rebuilding that bridge
            // cannot recapture it. The target applies live, so only the WS
            // bridge needs a rebuild after a control snapshot change.
            if (_bridge is not LocalDirectBridge)
                _needBridgeRebuild = true;
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

        private static string Truncate(string value, int maxLength)
        {
            if (string.IsNullOrEmpty(value)) return "";
            return value.Length <= maxLength ? value : value[..maxLength] + "...";
        }

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
