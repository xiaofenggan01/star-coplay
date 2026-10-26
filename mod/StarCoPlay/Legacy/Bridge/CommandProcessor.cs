using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using StarCoPlay.Actions;
using StarCoPlay.GameServices.Movement;
using StarCoPlay.GameServices.SimulatedInput;
using StarCoPlay.Logging;
using StarCoPlay.Models;
using StarCoPlay.V2;
using StarCoPlay.V2.Compound;
using StarCoPlay.V2.Models;
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
        private readonly ModLogger _logger;
        private readonly IBridge _bridge;
        private readonly EventEmitter _emitter;
        private readonly ActionExecutor _executor;
        private readonly StateReader _stateReader;
        private readonly AdapterV2 _adapterV2;
        private readonly IMoveToHandler _moveToHandler;
        private readonly AnimationAwaiter _animAwaiter;
        private readonly CompoundDispatcher _compoundDispatcher;
        private readonly ulong _bridgePlayerId;
        private readonly bool _verboseProtocolDiagnostics;

        /// <summary>当前执行中的命令。</summary>
        private AgentCommand? _activeCommand;

        /// <summary>当前执行中的批量操作。</summary>
        private IBatchAction? _activeBatchAction;

        /// <summary>当前执行中的 V2 异步命令。</summary>
        private V2Command? _pendingV2Command;

        /// <summary>V2 异步命令已等待的帧数。</summary>
        private int _pendingV2Frames;

        /*── HUD 公开属性 ──*/

        /// <summary>是否有 V2 命令正在执行（供 HUD 显示）。</summary>
        public bool HasPendingV2Command => _pendingV2Command != null;

        /// <summary>当前 pending V2 命令的 capability 名称（供 HUD 显示）。</summary>
        public string? PendingV2Capability => _pendingV2Command?.Capability;

        /// <summary>当前 pending V2 命令的 seq（供 HUD 显示）。</summary>
        public int PendingV2Seq => _pendingV2Command?.Seq ?? -1;

        /// <summary>当前 pending V2 命令已等待帧数（供 HUD 显示）。</summary>
        public int PendingV2Frames => _pendingV2Frames;

        /// <summary>Proto scheduler 用：旧链路是否正忙（任何活跃命令/动作/动画）。</summary>
        public bool IsBusyForProtocol =>
            _pendingV2Command != null
            || _compoundDispatcher.IsActive
            || _moveToHandler.IsActive
            || _animAwaiter.IsWaiting
            || _executor.IsBusy
            || _activeBatchAction != null
            || _activeCommand != null;

        /// <summary>Proto scheduler 用：旧链路忙碌原因（debug 用）。</summary>
        public string ProtocolBusyReason
        {
            get
            {
                if (_pendingV2Command != null) return $"v2_pending={_pendingV2Command.Capability}";
                if (_compoundDispatcher.IsActive) return "compound_active";
                if (_moveToHandler.IsActive) return "move_to_active";
                if (_animAwaiter.IsWaiting) return "anim_awaiter";
                if (_executor.IsBusy) return "executor_busy";
                if (_activeBatchAction != null) return "batch_active";
                if (_activeCommand != null) return "legacy_cmd_active";
                return "idle";
            }
        }

        /// <summary>最近完成的 V2 命令结果记录（供 HUD 显示，环形缓冲）。</summary>
        private readonly Queue<V2CommandRecord> _recentV2Commands = new();
        private const int MaxRecentV2Commands = 3;

        /// <summary>最近 V2 命令记录。</summary>
        public IReadOnlyCollection<V2CommandRecord> RecentV2Commands => _recentV2Commands;

        /// <summary>等待异步 V2 capability 完成的最长帧数（约 10 秒）。</summary>
        private const int MaxPendingV2Frames = 600;

        /// <summary>等待 compound capability 完成的最长帧数（约 2 分钟）。</summary>
        private const int MaxCompoundFrames = 7200;

        /// <summary>FormatKeyParams 提取的关键参数名（避免每次调用分配数组）。</summary>
        private static readonly string[] KeyParamNames = { "x", "y", "location", "target", "target_tile", "item", "direction", "text", "emote_id", "type", "slot" };

        /// <summary>V2 pending 命令的目标位置（用于完成时构建精确结果）。</summary>
        private (int x, int y, string? location)? _pendingV2Target;

        private int _pendingV2LastDebugFrame;

        public CommandProcessor(
            IMonitor monitor,
            ModLogger logger,
            IBridge bridge,
            EventEmitter emitter,
            ActionExecutor executor,
            StateReader stateReader,
            AdapterV2 adapterV2,
            IMoveToHandler moveToHandler,
            AnimationAwaiter animAwaiter,
            CompoundDispatcher compoundDispatcher,
            ulong bridgePlayerId,
            bool verboseProtocolDiagnostics = false)
        {
            _monitor = monitor;
            _logger = logger;
            _bridge = bridge;
            _emitter = emitter;
            _executor = executor;
            _stateReader = stateReader;
            _adapterV2 = adapterV2;
            _moveToHandler = moveToHandler;
            _animAwaiter = animAwaiter;
            _compoundDispatcher = compoundDispatcher;
            _bridgePlayerId = bridgePlayerId;
            _verboseProtocolDiagnostics = verboseProtocolDiagnostics;
        }

        /// <summary>
        /// Submit a V2 command through the same runtime path used by bridge polling.
        /// Immediate commands write a result and return it; async commands become pending
        /// and return null until Poll() completes them on a later tick.
        /// </summary>
        public V2Result? SubmitV2Command(V2Command v2Cmd)
        {
            var v2Capability = v2Cmd.Capability ?? "";

            _logger.LogExecution($"Command received: seq={v2Cmd.Seq} capability={v2Cmd.Capability} params={FormatKeyParams(v2Cmd)} busy={BuildDebugStatusSummary()}");

            if (v2Cmd.TargetPlayerId.HasValue && v2Cmd.TargetPlayerId.Value != 0)
            {
                if (v2Cmd.TargetPlayerId.Value != _bridgePlayerId)
                {
                    _monitor.Log($"CommandProcessor: ignored command #{v2Cmd.Seq} for target_player_id={v2Cmd.TargetPlayerId}; bridge player is {_bridgePlayerId}", LogLevel.Trace);
                    return null;
                }
            }

            if (_animAwaiter.IsWaiting)
            {
                if (!IsPassthroughCapability(v2Capability))
                {
                    var busyResult = ResultBuilder.Failure(
                        v2Cmd.Seq,
                        v2Cmd.ActionId,
                        ResultBuilder.Errors.ExecutionFailed,
                        "Animation is still running; wait for it to finish. observe/query commands are allowed.");
                    _bridge.WriteV2Result(ResultBuilder.ToJson(busyResult));
                    return busyResult;
                }
            }
            else if (_activeCommand != null || _activeBatchAction != null || _pendingV2Command != null)
            {
                var busyDetail = BuildDebugStatusSummary();
                var busyResult = ResultBuilder.Failure(
                    v2Cmd.Seq,
                    v2Cmd.ActionId,
                    ResultBuilder.Errors.ExecutionFailed,
                    $"Agent is busy; wait for the current command to finish. {busyDetail}");
                _bridge.WriteV2Result(ResultBuilder.ToJson(busyResult));
                _monitor.Log($"[protocol] busy reject: incoming seq={v2Cmd.Seq} capability={v2Capability}; {busyDetail}", LogLevel.Warn);
                return busyResult;
            }

            var result = _adapterV2.Dispatch(v2Cmd);
            if (result != null)
            {
                _bridge.WriteV2Result(ResultBuilder.ToJson(result));
                _logger.LogBridge($"Wrote result: seq={v2Cmd.Seq}, success={result.Success}, code={result.Error?.Code ?? "OK"}");
                if (!result.Success)
                    _logger.LogExecution($"Command failed: seq={v2Cmd.Seq} capability={v2Cmd.Capability} code={result.Error?.Code} msg={result.Error?.Message}", LogLevel.Warn);
                RecordV2Result(v2Cmd.Seq, v2Capability,
                    result.Success ? "OK" : result.Error?.Code ?? "ERROR");
                return result;
            }

            _logger.LogBridge($"Command started asynchronously: seq={v2Cmd.Seq}, capability={v2Cmd.Capability}");
            _pendingV2Command = v2Cmd;
            _pendingV2Frames = 0;
            _pendingV2LastDebugFrame = 0;
            _pendingV2Target = ExtractTargetFromParams(v2Cmd);
            _monitor.Log($"[protocol] async pending: {BuildDebugStatusSummary()}", _verboseProtocolDiagnostics ? LogLevel.Info : LogLevel.Debug);
            return null;
        }

        public string GetDebugStatus()
        {
            var player = Game1.player;
            var location = Game1.currentLocation?.Name ?? "Unknown";
            var tile = player != null ? $"{(int)player.Tile.X},{(int)player.Tile.Y}" : "null";
            var menu = Game1.activeClickableMenu?.GetType().Name ?? "none";
            var controller = player?.controller?.GetType().Name ?? "none";
            var canMove = player?.canMove.ToString() ?? "null";
            var contextCanMove = Context.CanPlayerMove;
            var usingTool = player?.UsingTool.ToString() ?? "null";
            var isInBed = player?.isInBed.Value.ToString() ?? "null";
            return $"{BuildDebugStatusSummary()} player={location}({tile}) canMove={canMove} contextCanMove={contextCanMove} usingTool={usingTool} isInBed={isInBed} controller={controller} menu={menu}";
        }

        public void LogDebugStatus(string prefix = "[protocol] status")
        {
            _monitor.Log($"{prefix}: {GetDebugStatus()}", LogLevel.Info);
        }

        public bool CancelProtocolWork(string reason = "manual")
        {
            var hadWork = _pendingV2Command != null
                || _compoundDispatcher.IsActive
                || _moveToHandler.IsActive
                || _animAwaiter.IsWaiting
                || _executor.IsBusy
                || _activeBatchAction != null
                || _activeCommand != null;

            _monitor.Log($"[protocol] cancel requested: reason={reason}; before={GetDebugStatus()}", LogLevel.Warn);

            _animAwaiter.Cancel();
            _moveToHandler.Cancel();
            var compoundResult = _compoundDispatcher.Cancel();
            if (compoundResult != null)
            {
                _bridge.WriteV2Result(ResultBuilder.ToJson(compoundResult));
                if (_pendingV2Command != null)
                {
                    LogAsyncResult("cancel-compound", _pendingV2Command, compoundResult);
                    RecordV2Result(_pendingV2Command.Seq, _pendingV2Command.Capability,
                        compoundResult.Success ? "OK" : compoundResult.Error?.Code ?? "CANCELLED");
                }
                _monitor.Log($"[protocol] cancel compound result: success={compoundResult.Success} code={compoundResult.Error?.Code ?? "OK"} message={compoundResult.Error?.Message ?? ""}", LogLevel.Warn);
            }

            if (_activeBatchAction != null)
            {
                _activeBatchAction.Cancel();
                _activeBatchAction = null;
                _activeCommand = null;
            }

            _executor.CancelCurrent();

            if (_pendingV2Command != null && compoundResult == null)
            {
                var cancelled = ResultBuilder.Failure(
                    _pendingV2Command.Seq,
                    _pendingV2Command.ActionId,
                    ResultBuilder.Errors.Cancelled,
                    $"Cancelled by protocol_cancel: {reason}");
                _bridge.WriteV2Result(ResultBuilder.ToJson(cancelled));
                LogAsyncResult("cancel", _pendingV2Command, cancelled);
                RecordV2Result(_pendingV2Command.Seq, _pendingV2Command.Capability, "CANCELLED");
            }

            ClearPendingV2();
            _activeCommand = null;

            _monitor.Log($"[protocol] cancel complete: hadWork={hadWork}; after={GetDebugStatus()}", LogLevel.Info);
            return hadWork;
        }

        private string BuildDebugStatusSummary()
        {
            var pending = _pendingV2Command != null
                ? $"pendingV2(seq={_pendingV2Command.Seq},cap={_pendingV2Command.Capability},frames={_pendingV2Frames})"
                : "pendingV2=none";
            var compound = _compoundDispatcher.IsActive
                ? $"compound={_compoundDispatcher.GetDebugStatus()}"
                : "compound=none";
            var active = _activeCommand != null
                ? $"activeCommand(action={_activeCommand.Action},cmd={_activeCommand.CmdId})"
                : "activeCommand=none";
            var batch = _activeBatchAction != null ? "batch=active" : "batch=none";
            var move = _moveToHandler.IsActive ? "moveTo=active" : "moveTo=idle";
            var animation = _animAwaiter.IsWaiting ? "animation=waiting" : "animation=idle";
            var executor = _executor.IsBusy ? "executor=busy" : "executor=idle";
            return $"{pending}; {compound}; {active}; {batch}; {move}; {animation}; {executor}";
        }

        private void LogAsyncResult(string source, V2Command cmd, V2Result result)
        {
            var code = result.Error?.Code ?? "OK";
            var message = result.Error?.Message ?? result.Observation ?? "";
            _monitor.Log($"[protocol] async result {source}: seq={cmd.Seq} capability={cmd.Capability} success={result.Success} code={code} status={result.Status} frames={_pendingV2Frames} message={message}", result.Success ? LogLevel.Info : LogLevel.Warn);
        }

        private void MaybeLogPendingHeartbeat(string source)
        {
            if (_pendingV2Command == null)
                return;

            if (_pendingV2Frames == 1 || _pendingV2Frames - _pendingV2LastDebugFrame >= 120)
            {
                _pendingV2LastDebugFrame = _pendingV2Frames;
                _monitor.Log($"[protocol] async heartbeat {source}: {GetDebugStatus()}", _verboseProtocolDiagnostics ? LogLevel.Info : LogLevel.Debug);
            }
        }

        private void ClearPendingV2()
        {
            _pendingV2Command = null;
            _pendingV2Frames = 0;
            _pendingV2LastDebugFrame = 0;
            _pendingV2Target = null;
        }

        private void WritePendingFailureAndClear(string source, string errorCode, string message)
        {
            if (_pendingV2Command == null)
                return;

            var cmd = _pendingV2Command;
            var result = ResultBuilder.Failure(cmd.Seq, cmd.ActionId, errorCode, message);
            _bridge.WriteV2Result(ResultBuilder.ToJson(result));
            LogAsyncResult(source, cmd, result);
            RecordV2Result(cmd.Seq, cmd.Capability, errorCode);
            ClearPendingV2();
        }

        /// <summary>
        /// 主轮询方法，在 ModEntry.OnUpdateTicked 中每帧调用。
        /// </summary>
        public void Poll(bool readBridgeCommands = true)
        {
            try
            {
                // 0pre. 延迟清理残留对话框（由 InteractHandler.RequestDismissDialogue 请求）
                if (InteractHandler.PendingDismissFrames > 0)
                {
                    InteractHandler.PendingDismissFrames--;
                    if (InteractHandler.PendingDismissFrames == 0
                        && Game1.activeClickableMenu is StardewValley.Menus.DialogueBox)
                    {
                        _monitor.Log("[V2] Poll: 延迟清理残留对话框", LogLevel.Debug);
                        Game1.activeClickableMenu = null;
                    }
                }

                // 0a. AnimationAwaiter: tick animation wait and write deferred result
                if (_animAwaiter.IsWaiting)
                {
                    var animResult = _animAwaiter.Tick();
                    if (animResult != null)
                    {
                        _bridge.WriteV2Result(ResultBuilder.ToJson(animResult));
                        // Clear any associated pending V2 command state
                        if (_pendingV2Command != null)
                        {
                            LogAsyncResult("animation", _pendingV2Command, animResult);
                            RecordV2Result(_pendingV2Command.Seq, _pendingV2Command.Capability,
                                animResult.Success ? "OK" : animResult.Error?.Code ?? "ERROR");
                            ClearPendingV2();
                        }
                    }
                }

                // Steps 0-2: skip if animation is still pending (only read new commands below)
                if (!_animAwaiter.IsWaiting)
                {

                // 0. 如果有 V2 异步命令在执行 → 等待完成
                if (_pendingV2Command != null)
                {
                    // move_to 有自己的多阶段状态机，委托 MoveToHandler.Tick() 驱动
                    if (_pendingV2Command.Capability == AdapterV2.CapabilityMoveTo)
                    {
                        if (_moveToHandler.IsActive)
                        {
                            var moveResult = _moveToHandler.Tick();
                            if (moveResult != null)
                            {
                                _bridge.WriteV2Result(ResultBuilder.ToJson(moveResult));
                                LogAsyncResult("move_to", _pendingV2Command, moveResult);
                                RecordV2Result(_pendingV2Command.Seq, _pendingV2Command.Capability,
                                    moveResult.Success ? "OK" : moveResult.Error?.Code ?? "ERROR");
                                ClearPendingV2();
                            }
                            else
                            {
                                _pendingV2Frames++;
                                MaybeLogPendingHeartbeat("move_to");
                            }
                            return;
                        }
                        // MoveToHandler 不再 active 但 _pendingV2Command 未清理
                        // （WARP_TIMEOUT 等场景中 Reset() 先于 result 返回）→ 清理孤儿状态
                        _monitor.Log($"[V2] move_to pending 但 handler 已 inactive，清理孤儿状态 seq={_pendingV2Command.Seq}", LogLevel.Warn);
                        WritePendingFailureAndClear("move_to-orphan",
                            ResultBuilder.Errors.ExecutionFailed,
                            "move_to pending state became orphaned: MoveToHandler is inactive before returning a result");
                        return;
                    }

                    // compound capability 委托 CompoundDispatcher.Tick() 驱动
                    if (_compoundDispatcher.IsCompoundCapability(_pendingV2Command.Capability)
                        && _compoundDispatcher.IsActive)
                    {
                        _pendingV2Frames++;
                        // Compound timeout: ~2 minutes at 60fps (HIGH-3 fix)
                        if (_pendingV2Frames > MaxCompoundFrames)
                        {
                            _monitor.Log($"[Compound] Timeout: {_pendingV2Command.Capability} after {_pendingV2Frames} frames", LogLevel.Warn);
                            var cancelResult = _compoundDispatcher.Cancel();
                            if (cancelResult != null)
                            {
                                _bridge.WriteV2Result(ResultBuilder.ToJson(cancelResult));
                                LogAsyncResult("compound-timeout", _pendingV2Command, cancelResult);
                                RecordV2Result(_pendingV2Command.Seq, _pendingV2Command.Capability, "TIMEOUT");
                            }
                            ClearPendingV2();
                            return;
                        }

                        MaybeLogPendingHeartbeat("compound");

                        var compoundResult = _compoundDispatcher.Tick();
                        if (compoundResult != null)
                        {
                            _bridge.WriteV2Result(ResultBuilder.ToJson(compoundResult));
                            LogAsyncResult("compound", _pendingV2Command, compoundResult);
                            RecordV2Result(_pendingV2Command.Seq, _pendingV2Command.Capability,
                                compoundResult.Success ? "OK" : compoundResult.Error?.Code ?? "ERROR");
                            ClearPendingV2();
                        }
                        return;
                    }

                    // Compound capabilities are fully managed by CompoundDispatcher.
                    // Don't fall through to generic executor-based completion logic.
                    if (_compoundDispatcher.IsCompoundCapability(_pendingV2Command.Capability))
                    {
                        _pendingV2Frames++;
                        _monitor.Log($"[protocol] compound pending but dispatcher inactive: {GetDebugStatus()}", LogLevel.Warn);
                        WritePendingFailureAndClear("compound-orphan",
                            ResultBuilder.Errors.ExecutionFailed,
                            "compound pending state became orphaned: CompoundDispatcher is inactive before returning a result");
                        return;
                    }

                    // 其他 capability：等待 ActionExecutor 完成
                    if (_executor.IsBusy)
                    {
                        _pendingV2Frames++;
                        if (_pendingV2Frames > MaxPendingV2Frames)
                        {
                            _logger.LogExecution($"命令超时: seq={_pendingV2Command.Seq}, capability={_pendingV2Command.Capability}, 等待={_pendingV2Frames}帧", LogLevel.Warn);
                            _logger.LogError($"命令超时: seq={_pendingV2Command.Seq}, capability={_pendingV2Command.Capability}", level: LogLevel.Warn);
                            _bridge.WriteV2Result(ResultBuilder.ToJson(
                                ResultBuilder.Failure(
                                    _pendingV2Command.Seq,
                                    _pendingV2Command.ActionId,
                                    ResultBuilder.Errors.Timeout,
                                    $"capability={_pendingV2Command.Capability} 执行超时（{MaxPendingV2Frames} 帧）")));
                            _logger.LogBridge($"写入超时结果: seq={_pendingV2Command.Seq}");
                            RecordV2Result(_pendingV2Command.Seq, _pendingV2Command.Capability, "TIMEOUT");
                            _executor.CancelCurrent();
                            ClearPendingV2();
                        }
                        else
                        {
                            MaybeLogPendingHeartbeat("executor");
                        }
                        return;
                    }

                    var completed = ResultBuilder.Success(
                        _pendingV2Command.Seq,
                        _pendingV2Command.ActionId,
                        BuildPendingV2CompletionData(_pendingV2Command))
                        .WithObservation($"{_pendingV2Command.Capability} 执行完成");
                    _bridge.WriteV2Result(ResultBuilder.ToJson(completed));
                    LogAsyncResult("executor", _pendingV2Command, completed);
                    _logger.LogExecution($"命令完成: seq={_pendingV2Command.Seq}, capability={_pendingV2Command.Capability}, 耗时={_pendingV2Frames}帧");
                    _logger.LogBridge($"写入结果: seq={_pendingV2Command.Seq}, success=true");
                    RecordV2Result(_pendingV2Command.Seq, _pendingV2Command.Capability, "OK");
                    ClearPendingV2();
                }

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

                } // end if (!_animAwaiter.IsWaiting)

                // 3. 读取新命令
                if (!readBridgeCommands)
                    return;

                var commands = _bridge.ReadNewCommands();
                foreach (var cmd in commands)
                {
                    _logger.LogBridge($"读取命令: seq={cmd.Seq}, capability={cmd.Capability ?? cmd.Action}, action_id={cmd.ActionId ?? cmd.CmdId}");

                    // ── V2 命令走 AdapterV2 ──────────────────────────────
                    if (cmd.ProtocolVersion == "2.0")
                    {
                        // 构建 V2Command
                        var v2Cmd = new StarCoPlay.V2.Models.V2Command
                        {
                            Seq = cmd.Seq ?? Math.Abs(cmd.CmdId?.GetHashCode() ?? 0),
                            ProtocolVersion = "2.0",
                            Capability = cmd.Capability ?? cmd.Action ?? "",
                            ActionId = cmd.ActionId ?? cmd.CmdId ?? "",
                            Params = DeserializeParams(cmd.Params),
                            TargetPlayerId = cmd.TargetPlayerId
                        };

                        SubmitV2Command(v2Cmd);
                        continue;
                    }

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
                _logger.LogError($"CommandProcessor.Poll 异常: {ex.Message}", ex);
                _monitor.Log($"CommandProcessor.Poll 异常: {ex.Message}", LogLevel.Error);
            }
        }

        /// <summary>
        /// Capabilities that are allowed through while AnimationAwaiter is waiting.
        /// Observe and all query_* are read-only and should not be blocked.
        /// </summary>
        private static bool IsPassthroughCapability(string capability)
        {
            return capability == AdapterV2.CapabilityObserve
                || capability.StartsWith("query_", StringComparison.Ordinal);
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
                _logger.LogError($"CommandProcessor.DispatchCommand 异常: {ex.Message}", ex);
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

            bool ok = _executor.EquipItemByName(itemName).Success;
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
            if (_pendingV2Command != null)
            {
                _animAwaiter.Cancel();
                _moveToHandler.Cancel();
                _executor.CancelCurrent();
                _bridge.WriteV2Result(ResultBuilder.ToJson(
                    ResultBuilder.Failure(
                        _pendingV2Command.Seq,
                        _pendingV2Command.ActionId,
                        ResultBuilder.Errors.Cancelled,
                        $"已取消 {_pendingV2Command.Capability}")));
                RecordV2Result(_pendingV2Command.Seq, _pendingV2Command.Capability, "CANCELLED");
                ClearPendingV2();
            }

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

        /// <summary>
        /// 将 JsonElement params 转换为 Dictionary&lt;string, object&gt;（用于 V2 handler）。
        /// 嵌套 Object 递归解析为 Dictionary，Array 解析为 List，不再返回 raw JSON 字符串。
        /// </summary>
        private static Dictionary<string, object> DeserializeParams(JsonElement elem)
        {
            var result = new Dictionary<string, object>();
            if (elem.ValueKind != JsonValueKind.Object) return result;
            foreach (var prop in elem.EnumerateObject())
            {
                var val = DeserializeValue(prop.Value);
                if (val != null)
                    result[prop.Name] = val;
            }
            return result;
        }

        /// <summary>递归将 JsonElement 转换为原生 CLR 类型。</summary>
        private static object? DeserializeValue(JsonElement element)
        {
            return element.ValueKind switch
            {
                JsonValueKind.String => element.GetString() ?? "",
                JsonValueKind.Number => element.TryGetInt64(out var l) ? (object)l : element.GetDouble(),
                JsonValueKind.True => true,
                JsonValueKind.False => false,
                JsonValueKind.Object => DeserializeJsonObject(element),
                JsonValueKind.Array => DeserializeJsonArray(element),
                JsonValueKind.Null => null,
                _ => null,
            };
        }

        /// <summary>将 JsonElement（Object）递归解析为 Dictionary&lt;string, object&gt;。</summary>
        private static Dictionary<string, object> DeserializeJsonObject(JsonElement element)
        {
            var dict = new Dictionary<string, object>();
            foreach (var prop in element.EnumerateObject())
            {
                var val = DeserializeValue(prop.Value);
                if (val != null)
                    dict[prop.Name] = val;
            }
            return dict;
        }

        /// <summary>将 JsonElement（Array）递归解析为 List&lt;object&gt;。</summary>
        private static List<object> DeserializeJsonArray(JsonElement element)
        {
            var list = new List<object>();
            foreach (var item in element.EnumerateArray())
            {
                var val = DeserializeValue(item);
                if (val != null)
                    list.Add(val);
            }
            return list;
        }

        private object BuildPendingV2CompletionData(V2Command cmd)
        {
            var player = Game1.player;
            var finalX = (int)player.Tile.X;
            var finalY = (int)player.Tile.Y;
            var finalLocation = Game1.currentLocation?.Name ?? "Unknown";

            if (cmd.Capability == AdapterV2.CapabilityMoveTo)
            {
                var result = new Dictionary<string, object>
                {
                    ["capability"] = cmd.Capability,
                    ["status"] = "completed",
                    ["final_position"] = new Dictionary<string, object>
                    {
                        ["location"] = finalLocation,
                        ["x"] = finalX,
                        ["y"] = finalY
                    }
                };

                // 包含目标位置以便 Agent 判断是否精确到达
                if (_pendingV2Target.HasValue)
                {
                    var target = _pendingV2Target.Value;
                    result["target"] = new Dictionary<string, object>
                    {
                        ["x"] = target.x,
                        ["y"] = target.y,
                        ["location"] = target.location ?? finalLocation
                    };
                    result["reached_target"] = finalX == target.x && finalY == target.y;
                }

                return result;
            }

            return new
            {
                capability = cmd.Capability,
                status = "completed",
                final_position = new
                {
                    location = finalLocation,
                    x = finalX,
                    y = finalY
                }
            };
        }

        /// <summary>提取命令关键参数用于日志（避免全量序列化产生噪音）。</summary>
        private static string FormatKeyParams(V2Command cmd)
        {
            if (cmd.Params == null || cmd.Params.Count == 0)
                return "{}";

            var parts = new List<string>();
            foreach (var key in KeyParamNames)
            {
                if (cmd.Params.TryGetValue(key, out var val) && val != null)
                {
                    var s = val.ToString() ?? "";
                    if (s.Length > 40) s = s[..40] + "...";
                    parts.Add($"{key}={s}");
                }
            }
            return parts.Count > 0 ? string.Join(" ", parts) : $"({cmd.Params.Count} params)";
        }

        /// <summary>从 V2 命令参数中提取目标位置信息。</summary>
        private static (int x, int y, string? location)? ExtractTargetFromParams(V2Command cmd)
        {
            if (cmd.Params == null) return null;

            string? location = null;
            if (cmd.Params.TryGetValue("location", out var locObj) && locObj is string locStr)
                location = locStr;

            if (cmd.Params.TryGetValue("x", out var xObj) && cmd.Params.TryGetValue("y", out var yObj))
            {
                if (TryConvertToInt(xObj, out var x) && TryConvertToInt(yObj, out var y))
                    return (x, y, location);
            }

            if (cmd.Params.TryGetValue("target_tile", out var tileObj) && tileObj is Dictionary<string, object> tileDict)
            {
                if (tileDict.TryGetValue("x", out xObj) && tileDict.TryGetValue("y", out yObj))
                {
                    if (TryConvertToInt(xObj, out var x) && TryConvertToInt(yObj, out var y))
                        return (x, y, location);
                }
            }

            return location != null ? (0, 0, location) : null;
        }

        private static bool TryConvertToInt(object? raw, out int value)
        {
            value = default;
            switch (raw)
            {
                case int i: value = i; return true;
                case long l when l >= int.MinValue && l <= int.MaxValue: value = (int)l; return true;
                case double d when d >= int.MinValue && d <= int.MaxValue: value = (int)d; return true;
                case string s when int.TryParse(s, out var parsed): value = parsed; return true;
                default: return false;
            }
        }

        /// <summary>记录一条 V2 命令结果到环形缓冲（供 HUD 显示）。</summary>
        private void RecordV2Result(int seq, string capability, string status)
        {
            _recentV2Commands.Enqueue(new V2CommandRecord
            {
                Seq = seq,
                Capability = capability,
                Status = status,
                Timestamp = DateTime.Now
            });
            while (_recentV2Commands.Count > MaxRecentV2Commands)
                _recentV2Commands.Dequeue();
        }
    }

    /// <summary>V2 命令执行记录（供 HUD 显示）。</summary>
    public class V2CommandRecord
    {
        public int Seq { get; set; }
        public string Capability { get; set; } = "";
        public string Status { get; set; } = "";
        public DateTime Timestamp { get; set; }

        public override string ToString() =>
            $"#{Seq} {Capability} → {Status}";
    }
}
