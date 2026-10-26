using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Globalization;
using System.Linq;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Google.Protobuf;
using NewtonsoftJson = Newtonsoft.Json.JsonConvert;
using StarCoPlay.Logging;
using StarCoPlay.Models;
using StarCoPlay.Protocol;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.Models;
using StardewModdingAPI;

namespace StarCoPlay.Bridge
{
    /// <summary>WsBridge 连接状态，供 GMCM 和 HUD 读取。</summary>
    public enum WsConnectionState
    {
        Disconnected,   // 未连接（初始 / 手动断开）
        Connecting,     // 正在连接中
        Connected,      // 已连接且握手成功
        Reconnecting,   // 断线重连中（含 backoff 等待）
        AuthError,      // 认证失败（fatal，不可重连）
        SessionEnded,   // 服务端主动结束会话（fatal，不可重连）
    }

    public class WsBridge : IBridge, IProtocolBridge
    {
        private readonly IMonitor _monitor;
        private readonly string _url;
        private readonly string _platformToken;
        private readonly string _modVersion;
        private readonly ulong? _playerUniqueId;
        private readonly string _bridgeDir;
        private readonly HostedRouteHello _routeHello;
        private readonly AgentControlSnapshot _controlSnapshot;

        private ModLogger? _logger;
        private ClientWebSocket? _ws;
        private CancellationTokenSource? _cts;
        private Task? _connectLoopTask;
        private readonly BlockingCollection<string> _sendQueue = new(new ConcurrentQueue<string>(), 500);
        private readonly ConcurrentQueue<AgentCommand> _pendingCommands = new();
        private readonly ConcurrentQueue<Envelope> _pendingProtocolEnvelopes = new();
        private readonly ConcurrentDictionary<string, ProtocolPendingContext> _pendingProtocolContexts = new();
        private int _seq;
        private volatile bool _connected;
        private string? _agentInstanceId;
        private string? _resumeToken;
        private volatile string? _connectionRole;
        private long _acceptedAgentControlRevision = -1;
        private volatile bool _controllerRecoveryAllowed;
        private long _lastServerActivityTicks;
        private readonly Random _rand = new();
        private readonly object _wsLock = new();
        private readonly SemaphoreSlim _sendLock = new(1, 1);
        private volatile bool _explicitGameSessionEnded;

        // ── 连接状态（Phase 1 新增）──
        private volatile WsConnectionState _state = WsConnectionState.Disconnected;
        private volatile string? _fatalReason;
        private volatile string? _agentStatus;
        private volatile string? _pendingStateSnapshot;
        private volatile string? _pendingPong;

        /// <summary>连接状态（线程安全只读）。</summary>
        public WsConnectionState ConnectionState => _state;
        /// <summary>fatal 错误原因（auth_error / session.end 时非 null）。</summary>
        public string? FatalReason => _fatalReason;
        /// <summary>Gateway 分配的 Agent 实例 ID。</summary>
        public string? AgentInstanceId => _agentInstanceId;
        /// <summary>Agent 当前状态：thinking / idle / executing。</summary>
        public string? AgentStatus => _agentStatus;
        public string? ConnectionRole => _connectionRole;
        public long AcceptedAgentControlRevision => Interlocked.Read(ref _acceptedAgentControlRevision);
        public bool ControllerRecoveryAllowed => _controllerRecoveryAllowed;
        /// <summary>是否已连接。</summary>
        public bool IsConnected => _state == WsConnectionState.Connected;
        /// <summary>状态变更事件（后台线程触发，消费者需注意线程安全）。</summary>
        public event Action<WsConnectionState, string?>? ConnectionStateChanged;
        public event Action<string, bool>? HostedStatusReceived;

        private const int HeartbeatIntervalMs = 15000;
        private const int ServerSilenceTimeoutMs = 45000;
        private const int MaxBackoffMs = 30000;
        private static readonly TimeSpan StableConnectionBackoffReset = TimeSpan.FromSeconds(30);

        internal static bool ShouldResetReconnectBackoff(bool authenticated, TimeSpan connectionLifetime)
            => authenticated && connectionLifetime >= StableConnectionBackoffReset;

        private static readonly JsonSerializerOptions JsonOpts = new()
        {
            PropertyNamingPolicy = new SnakeCaseNamingPolicy(),
            WriteIndented = false,
        };

        private sealed class ProtocolPendingContext
        {
            public string RequestId { get; init; } = "";
            public string SessionId { get; init; } = "";
            public string ProtocolCapability { get; init; } = "";
            public ProtocolMappingDiagnostics Diagnostics { get; init; } = new();
        }

        public WsBridge(IMonitor monitor, string modDir, string url, string platformToken, string modVersion,
            ulong? playerUniqueId = null, HostedRouteHello? routeHello = null,
            AgentControlSnapshot? controlSnapshot = null)
        {
            _monitor = monitor;
            _url = url;
            _platformToken = platformToken;
            _modVersion = modVersion ?? "";
            _playerUniqueId = playerUniqueId;
            _routeHello = routeHello ?? throw new ArgumentNullException(nameof(routeHello));
            _controlSnapshot = controlSnapshot ?? throw new ArgumentNullException(nameof(controlSnapshot));
            _bridgeDir = Path.Combine(modDir, playerUniqueId.HasValue ? $"bridge_{playerUniqueId.Value}" : "bridge");
        }

        public string BridgeDir => _bridgeDir;

        public void SetLogger(ModLogger logger) => _logger = logger;

        public void EnsureDirectoryExists()
        {
            try { Directory.CreateDirectory(_bridgeDir); }
            catch (Exception ex) { _monitor.Log($"WsBridge EnsureDirectoryExists: {ex.Message}", LogLevel.Warn); }
        }

        public void Initialize()
        {
            EnsureDirectoryExists();
            _seq = 0;
            _state = WsConnectionState.Disconnected;
            _fatalReason = null;
            _agentStatus = null;

            if (string.IsNullOrWhiteSpace(_platformToken))
            {
                _monitor.Log("WsBridge: 未配置 PlatformToken，跳过连接循环（请在 GMCM 填入 Token）", LogLevel.Warn);
                _logger?.LogBridge("Bridge WS idle: no platform token", LogLevel.Warn);
                return;
            }

            _cts = new CancellationTokenSource();
            _connectLoopTask = Task.Run(() => RunConnectLoopAsync(_cts.Token));
        }

        private void SetState(WsConnectionState newState, string? reason = null)
        {
            _state = newState;
            ConnectionStateChanged?.Invoke(newState, reason);
        }

        private async Task RunConnectLoopAsync(CancellationToken token)
        {
            int attempt = 0;
            while (!token.IsCancellationRequested)
            {
                SetState(attempt == 0 ? WsConnectionState.Connecting : WsConnectionState.Reconnecting);
                var connectionStartedAt = DateTime.UtcNow;

                ClientWebSocket ws;
                lock (_wsLock)
                {
                    _ws = new ClientWebSocket();
                    ws = _ws;
                }
                try
                {
                    await ws.ConnectAsync(new Uri(_url), token).ConfigureAwait(false);
                    await SendHelloAsync(ws, token).ConfigureAwait(false);
                    _connected = true;
                    _lastServerActivityTicks = DateTime.UtcNow.Ticks;
                    // Connected 状态在收到 hello_ok 时设置（HandleMessage）
                    _monitor.Log($"WsBridge socket opened, awaiting hello_ok: {_url}", LogLevel.Info);
                    _logger?.LogBridge($"Bridge WS socket opened: url={_url}", LogLevel.Info);

                    using var sessionCts = CancellationTokenSource.CreateLinkedTokenSource(token);
                    var recvTask = Task.Run(() => ReceiveLoopAsync(ws, sessionCts.Token), sessionCts.Token);
                    var sendTask = Task.Run(() => SendLoopAsync(ws, sessionCts.Token), sessionCts.Token);
                    var heartbeatTask = Task.Run(() => HeartbeatLoopAsync(ws, sessionCts.Token), sessionCts.Token);

                    var finished = await Task.WhenAny(recvTask, sendTask, heartbeatTask).ConfigureAwait(false);
                    sessionCts.Cancel();
                    try { await Task.WhenAll(recvTask, sendTask, heartbeatTask).ConfigureAwait(false); }
                    catch { }
                }
                catch (OperationCanceledException) { }
                catch (Exception ex)
                {
                    _monitor.Log($"WsBridge conn lost: {ex.Message}", LogLevel.Warn);
                }
                finally
                {
                    _connected = false;
                    try { ws.Abort(); } catch { }
                    try { ws.Dispose(); } catch { }
                }

                // fatal 状态（auth_error / session.end）已在 HandleMessage 中 cancel _cts
                if (token.IsCancellationRequested) break;

                if (ShouldResetReconnectBackoff(
                    _state == WsConnectionState.Connected,
                    DateTime.UtcNow - connectionStartedAt))
                    attempt = 0;

                // 非 fatal 断线 → Reconnecting
                if (_state != WsConnectionState.AuthError && _state != WsConnectionState.SessionEnded)
                    SetState(WsConnectionState.Reconnecting);

                var pow = Math.Min(attempt, 5);
                var backoff = Math.Min(MaxBackoffMs, 1000 * (int)Math.Pow(2, pow)) + _rand.Next(0, 500);
                attempt++;
                _monitor.Log($"WsBridge reconnect in {backoff}ms (attempt {attempt})", LogLevel.Info);
                try { await Task.Delay(backoff, token).ConfigureAwait(false); }
                catch (OperationCanceledException) { break; }
            }

            // 循环退出：如果不是 fatal 状态，设为 Disconnected
            if (_state != WsConnectionState.AuthError && _state != WsConnectionState.SessionEnded)
                SetState(WsConnectionState.Disconnected);
        }

        private async Task SendHelloAsync(ClientWebSocket ws, CancellationToken token)
        {
            var hello = new Dictionary<string, object?>
            {
                ["type"] = WsProtocol.MsgHello,
                ["protocol_version"] = WsProtocol.ProtocolVersion,
                ["platform_token"] = _platformToken,
                ["mod_version"] = _modVersion,
                ["player_id"] = _playerUniqueId?.ToString(CultureInfo.InvariantCulture),
                ["agent_control_enabled"] = _controlSnapshot.Enabled,
                ["agent_control_target_player_id"] = _controlSnapshot.TargetPlayerId,
                ["agent_control_revision"] = _controlSnapshot.Revision,
                ["route_contract_version"] = HostedRouteHello.ContractVersion,
                ["game_instance_nonce"] = _routeHello.GameInstanceNonce,
                ["capability_digest"] = _routeHello.CapabilityDigest,
                ["capability_entries"] = _routeHello.CapabilityEntries.Select(entry =>
                    new Dictionary<string, string>
                    {
                        ["source_surface"] = entry.SourceSurface,
                        ["capability_id"] = entry.CapabilityId,
                        ["capability_contract_version"] = entry.CapabilityContractVersion,
                    }).ToArray(),
            };
            if (!string.IsNullOrEmpty(_resumeToken))
                hello["resume_token"] = _resumeToken;
            await SendRawAsync(ws, JsonSerializer.Serialize(hello), token).ConfigureAwait(false);
        }

        private async Task SendLoopAsync(ClientWebSocket ws, CancellationToken token)
        {
            try
            {
                while (!token.IsCancellationRequested && ws.State == WebSocketState.Open)
                {
                    // 最高优先级：pong 响应（不走队列，防止队列满时丢弃导致 Gateway 超时）
                    var pong = Interlocked.Exchange(ref _pendingPong, null);
                    if (pong != null)
                        await SendRawAsync(ws, pong, token).ConfigureAwait(false);

                    // 优先发送最新 state snapshot（覆盖策略，不积压）
                    var snapshot = Interlocked.Exchange(ref _pendingStateSnapshot, null);
                    if (snapshot != null)
                        await SendRawAsync(ws, snapshot, token).ConfigureAwait(false);

                    // 从队列取消息（100ms 超时，避免阻塞 snapshot 发送）
                    if (_sendQueue.TryTake(out var msg, 100, token))
                    {
                        if (ws.State != WebSocketState.Open) return;
                        await SendRawAsync(ws, msg, token).ConfigureAwait(false);
                    }
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception ex) { _monitor.Log($"WsBridge send loop: {ex.Message}", LogLevel.Warn); }
        }

        private async Task HeartbeatLoopAsync(ClientWebSocket ws, CancellationToken token)
        {
            try
            {
                while (!token.IsCancellationRequested && ws.State == WebSocketState.Open)
                {
                    await Task.Delay(HeartbeatIntervalMs, token).ConfigureAwait(false);
                    var silenceMs = (DateTime.UtcNow.Ticks - Interlocked.Read(ref _lastServerActivityTicks)) / TimeSpan.TicksPerMillisecond;
                    if (silenceMs > ServerSilenceTimeoutMs)
                    {
                        _monitor.Log($"WsBridge server silent {silenceMs}ms, forcing reconnect", LogLevel.Warn);
                        try { ws.Abort(); } catch { }
                        return;
                    }
                    var ping = JsonSerializer.Serialize(new Dictionary<string, object?>
                    {
                        ["type"] = WsProtocol.MsgPing,
                        ["ts"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                    });
                    try { await SendRawAsync(ws, ping, token).ConfigureAwait(false); }
                    catch { return; }
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception ex) { _monitor.Log($"WsBridge heartbeat: {ex.Message}", LogLevel.Warn); }
        }

        private async Task SendRawAsync(ClientWebSocket ws, string msg, CancellationToken token)
        {
            var bytes = Encoding.UTF8.GetBytes(msg);
            await _sendLock.WaitAsync(token).ConfigureAwait(false);
            try
            {
                await ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, token).ConfigureAwait(false);
            }
            finally
            {
                _sendLock.Release();
            }
        }

        private async Task ReceiveLoopAsync(ClientWebSocket ws, CancellationToken token)
        {
            var buffer = new byte[16 * 1024];
            var sb = new StringBuilder();
            try
            {
                while (ws.State == WebSocketState.Open && !token.IsCancellationRequested)
                {
                    sb.Clear();
                    WebSocketReceiveResult res;
                    do
                    {
                        res = await ws.ReceiveAsync(new ArraySegment<byte>(buffer), token).ConfigureAwait(false);
                        if (res.MessageType == WebSocketMessageType.Close)
                        {
                            return;
                        }
                        sb.Append(Encoding.UTF8.GetString(buffer, 0, res.Count));
                    } while (!res.EndOfMessage);

                    Interlocked.Exchange(ref _lastServerActivityTicks, DateTime.UtcNow.Ticks);
                    HandleMessage(sb.ToString());
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception ex) { _monitor.Log($"WsBridge recv loop: {ex.Message}", LogLevel.Warn); }
        }

        private void HandleMessage(string json)
        {
            try
            {
                using var doc = JsonDocument.Parse(json);
                var root = doc.RootElement;
                if (!root.TryGetProperty("type", out var typeEl))
                {
                    _monitor.Log($"WsBridge recv unknown frame (no type): {json.Substring(0, Math.Min(200, json.Length))}", LogLevel.Warn);
                    return;
                }
                var t = typeEl.GetString();
                if (t != WsProtocol.MsgPong && t != WsProtocol.MsgPing)
                    _monitor.Log($"WsBridge recv: type={t} size={json.Length}", LogLevel.Trace);
                switch (t)
                {
                    case WsProtocol.MsgHelloOk:
                        _controllerRecoveryAllowed = false;
                        if (root.TryGetProperty("agent_instance_id", out var aid))
                            _agentInstanceId = aid.GetString();
                        if (root.TryGetProperty("resume_token", out var rt))
                            _resumeToken = rt.GetString();
                        _connectionRole = root.TryGetProperty("connection_role", out var roleEl)
                            ? roleEl.GetString() : null;
                        if (root.TryGetProperty("accepted_agent_control_revision", out var revisionEl)
                            && revisionEl.TryGetInt64(out var acceptedRevision))
                            Interlocked.Exchange(ref _acceptedAgentControlRevision, acceptedRevision);
                        SetState(WsConnectionState.Connected);
                        _monitor.Log($"WsBridge authenticated: instance={_agentInstanceId} role={_connectionRole ?? "unknown"} accepted_control_revision={AcceptedAgentControlRevision} resumed={(root.TryGetProperty("resumed", out var rEl) && rEl.ValueKind == JsonValueKind.True)}", LogLevel.Info);
                        _logger?.LogBridge($"Bridge WS authenticated: instance={_agentInstanceId}", LogLevel.Info);
                        break;
                    case "connection_role_changed":
                        _connectionRole = root.TryGetProperty("connection_role", out var changedRoleEl)
                            ? changedRoleEl.GetString() : _connectionRole;
                        if (root.TryGetProperty("accepted_agent_control_revision", out var changedRevisionEl)
                            && changedRevisionEl.TryGetInt64(out var changedRevision))
                            Interlocked.Exchange(ref _acceptedAgentControlRevision, changedRevision);
                        if (string.Equals(_connectionRole, "controller", StringComparison.OrdinalIgnoreCase))
                            _controllerRecoveryAllowed = false;
                        _monitor.Log(
                            $"WsBridge role changed: role={_connectionRole ?? "unknown"} accepted_control_revision={AcceptedAgentControlRevision}",
                            LogLevel.Info);
                        break;
                    case WsProtocol.MsgHostedStatus:
                        var statusCode = root.TryGetProperty("code", out var statusCodeEl)
                            ? statusCodeEl.GetString() : null;
                        var statusRetryable = root.TryGetProperty("retryable", out var statusRetryableEl)
                            && statusRetryableEl.ValueKind == JsonValueKind.True;
                        if (string.Equals(statusCode, "controller_recovery_required", StringComparison.Ordinal))
                        {
                            _controllerRecoveryAllowed = statusRetryable;
                            if (root.TryGetProperty("accepted_agent_control_revision", out var statusRevisionEl)
                                && statusRevisionEl.TryGetInt64(out var statusRevision))
                                Interlocked.Exchange(ref _acceptedAgentControlRevision, statusRevision);
                        }
                        else
                        {
                            _controllerRecoveryAllowed = false;
                        }
                        _monitor.Log($"WsBridge hosted status: code={statusCode ?? "unknown"} retryable={_controllerRecoveryAllowed}", LogLevel.Info);
                        HostedStatusReceived?.Invoke(statusCode ?? "unknown", statusRetryable);
                        break;
                    case WsProtocol.MsgCommand:
                        var controlKind = ControlFramingCodec.ClassifyV2(root, out _);
                        var cmd = new AgentCommand
                        {
                            ActionId = root.TryGetProperty("action_id", out var aEl) ? aEl.GetString() : null,
                            Capability = root.TryGetProperty("capability", out var cEl) ? cEl.GetString() : null,
                            Seq = root.TryGetProperty("seq", out var sEl) ? sEl.GetInt32() : (int?)null,
                            ProtocolVersion = "2.0",
                            Params = root.TryGetProperty("params", out var pEl) ? pEl.Clone() : default,
                            TargetPlayerId = root.TryGetProperty("target_player_id", out var tpEl) && tpEl.ValueKind == JsonValueKind.Number
                                ? tpEl.GetUInt64() : (ulong?)null,
                        };
                        if (controlKind == ControlFrameKind.V2Cancel)
                        {
                            cmd.Action = "__cancel__";
                        }
                        _pendingCommands.Enqueue(cmd);
                        _monitor.Log($"WsBridge cmd received: seq={cmd.Seq} cap={cmd.Capability} action_id={cmd.ActionId} target={cmd.TargetPlayerId}", LogLevel.Info);
                        _logger?.LogBridge($"Bridge WS cmd received: seq={cmd.Seq} cap={cmd.Capability} action_id={cmd.ActionId}", LogLevel.Info);
                        break;
                    case WsProtocol.MsgProtocolEnvelope:
                        HandleProtocolEnvelope(root);
                        break;
                    case WsProtocol.MsgPing:
                        // pong 走直通道（Interlocked），不走 sendQueue，避免队列满时丢弃导致 Gateway 超时
                        var pongMsg = JsonSerializer.Serialize(new Dictionary<string, object?>
                        {
                            ["type"] = WsProtocol.MsgPong,
                            ["ts"] = root.TryGetProperty("ts", out var tsEl) ? (object?)tsEl.Clone() : null,
                        });
                        Interlocked.Exchange(ref _pendingPong, pongMsg);
                        break;
                    case WsProtocol.MsgPong:
                        break;
                    case WsProtocol.MsgAuthError:
                        {
                            var code = root.TryGetProperty("code", out var ceEl) ? ceEl.GetString() : null;
                            var msg2 = root.TryGetProperty("message", out var meEl) ? meEl.GetString() : null;
                            _fatalReason = code ?? "unknown";
                            SetState(WsConnectionState.AuthError, code);
                            _monitor.Log($"WsBridge auth error: code={code} message={msg2} — 停止重连", LogLevel.Error);
                            _logger?.LogBridge($"Bridge WS auth error: code={code} message={msg2}", LogLevel.Error);
                            _cts?.Cancel();
                        }
                        break;
                    case WsProtocol.MsgSessionEnd:
                        var endReason = root.TryGetProperty("reason", out var erEl) ? erEl.GetString() : "server_ended";
                        _fatalReason = endReason;
                        SetState(WsConnectionState.SessionEnded, endReason);
                        _monitor.Log($"WsBridge session ended: {endReason} — 停止重连", LogLevel.Warn);
                        _cts?.Cancel();
                        break;
                    case WsProtocol.MsgAgentStatus:
                        if (root.TryGetProperty("status", out var statusEl))
                            _agentStatus = statusEl.GetString();
                        break;
                    default:
                        _monitor.Log($"WsBridge recv unhandled type={t}", LogLevel.Debug);
                        break;
                }
            }
            catch (Exception ex)
            {
                _monitor.Log($"WsBridge handle message: {ex.Message}", LogLevel.Warn);
            }
        }

        private void HandleProtocolEnvelope(JsonElement root)
        {
            if (!root.TryGetProperty("payload", out var payloadEl))
            {
                _monitor.Log("WsBridge protocol_envelope missing payload", LogLevel.Warn);
                return;
            }

            var payload = payloadEl.GetString();
            if (string.IsNullOrWhiteSpace(payload))
            {
                _monitor.Log("WsBridge protocol_envelope empty payload", LogLevel.Warn);
                return;
            }

            try
            {
                Envelope envelope;
                try { envelope = ControlFramingCodec.DecodeRequestBase64(payload); }
                catch (InvalidOperationException)
                {
                    envelope = Envelope.Parser.ParseFrom(Convert.FromBase64String(payload));
                }

                var controlKind = ControlFramingCodec.Classify(envelope);
                if (envelope.BodyCase == Envelope.BodyOneofCase.CommandRequest
                    || controlKind != ControlFrameKind.NotControl)
                {
                    _pendingProtocolEnvelopes.Enqueue(envelope);
                    var cmdId = envelope.BodyCase switch
                    {
                        Envelope.BodyOneofCase.CommandRequest => envelope.CommandRequest.CommandId,
                        Envelope.BodyOneofCase.CommandCancel => envelope.CommandCancel.CommandId,
                        _ => envelope.CommandStatusRequest.CommandId,
                    };
                    _monitor.Log($"WsBridge proto enqueued: body={envelope.BodyCase} request_id={envelope.RequestId} command_id={cmdId}", LogLevel.Info);
                    return;
                }

                _monitor.Log($"WsBridge protocol_envelope ignored body={envelope.BodyCase}", LogLevel.Debug);
            }
            catch (Exception ex)
            {
                _monitor.Log($"WsBridge protocol_envelope parse failed: {ex.Message}", LogLevel.Warn);
            }
        }

        private void TryWriteProtocolMappingError(JsonElement root, ProtocolMappingException ex)
        {
            try
            {
                var payload = root.TryGetProperty("payload", out var payloadEl) ? payloadEl.GetString() : "";
                if (string.IsNullOrWhiteSpace(payload))
                    return;

                var envelope = Envelope.Parser.ParseFrom(Convert.FromBase64String(payload));
                var result = ProtoResultMapper.FromMappingException(envelope.CommandRequest, ex);
                var response = BuildProtocolResultEnvelope(envelope.RequestId, envelope.SessionId, result);
                EnqueueProtocolEnvelope(response, envelope.RequestId, result.Capability, result.CommandId);
            }
            catch (Exception writeEx)
            {
                _monitor.Log($"WsBridge protocol rejection response failed: {writeEx.Message}", LogLevel.Warn);
            }
        }

        private void EnqueueRaw(string msg)
        {
            if (!_sendQueue.TryAdd(msg))
                _monitor.Log("WsBridge sendQueue full, dropping message", LogLevel.Warn);
        }

        /// <summary>Send a raw JSON string message to the Gateway (public access for AppearanceSync etc).</summary>
        public void SendRaw(string json)
        {
            EnqueueRaw(json);
        }

        public void WriteAdapterInfo(string json)
        {
            try
            {
                using var doc = JsonDocument.Parse(json);
                var msg = new Dictionary<string, object?>
                {
                    ["type"] = "adapter_info",
                    ["adapter_info"] = JsonSerializer.Deserialize<JsonElement>(json),
                };
                EnqueueRaw(JsonSerializer.Serialize(msg, JsonOpts));
            }
            catch (Exception ex) { _monitor.Log($"WsBridge WriteAdapterInfo: {ex.Message}", LogLevel.Warn); }
        }

        public void Shutdown()
        {
            try
            {
                _sendQueue.CompleteAdding();
                _cts?.Cancel();
                ClientWebSocket? ws;
                lock (_wsLock) { ws = _ws; }
                if (ws != null && ws.State == WebSocketState.Open)
                {
                    try { ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "shutdown", CancellationToken.None).GetAwaiter().GetResult(); }
                    catch { }
                }
                try { _connectLoopTask?.Wait(2000); } catch { }
            }
            catch (Exception ex) { _monitor.Log($"WsBridge Shutdown: {ex.Message}", LogLevel.Warn); }
            finally
            {
                _connected = false;
                SetState(WsConnectionState.Disconnected);
            }
        }

        /// <summary>
        /// 明确结束当前游戏存档会话。该帧与正常关闭码共同告诉 Gateway
        /// 立即释放 Controller；只有没有此语义的异常断线才允许 resume。
        /// </summary>
        public void EndGameSession(string reason = "returned_to_title")
        {
            if (_explicitGameSessionEnded) return;
            _explicitGameSessionEnded = true;
            try
            {
                ClientWebSocket? ws;
                lock (_wsLock) { ws = _ws; }
                if (ws != null && ws.State == WebSocketState.Open)
                {
                    var frame = JsonSerializer.Serialize(
                        new { type = WsProtocol.MsgSessionEnd, reason }, JsonOpts);
                    using var timeout = new CancellationTokenSource(TimeSpan.FromMilliseconds(1500));
                    SendRawAsync(ws, frame, timeout.Token).GetAwaiter().GetResult();
                    ws.CloseAsync(WebSocketCloseStatus.NormalClosure, reason, timeout.Token)
                        .GetAwaiter().GetResult();
                }
            }
            catch (Exception ex)
            {
                _monitor.Log($"WsBridge explicit game session close failed [{ex.GetType().Name}]", LogLevel.Warn);
            }
        }

        /// <summary>
        /// 立即强制中止所有 socket 活动，不做优雅关闭。
        /// 用于 ProcessExit 等无法阻塞等待的场景，防止 macOS native socket 竞争崩溃。
        /// </summary>
        public void ForceAbort()
        {
            try { _cts?.Cancel(); } catch { }
            try
            {
                ClientWebSocket? ws;
                lock (_wsLock) { ws = _ws; }
                ws?.Abort();
            }
            catch { }
            _connected = false;
        }

        public void WriteV2Result(string jsonLine)
        {
            try
            {
                var v2 = NewtonsoftJson.DeserializeObject<V2Result>(jsonLine);
                if (v2 != null && !string.IsNullOrWhiteSpace(v2.ActionId)
                    && _pendingProtocolContexts.TryRemove(v2.ActionId, out var context))
                {
                    var commandResult = ProtoResultMapper.FromV2Result(
                        context.ProtocolCapability,
                        v2,
                        context.Diagnostics);
                    var envelope = BuildProtocolResultEnvelope(
                        context.RequestId,
                        context.SessionId,
                        commandResult);
                    EnqueueProtocolEnvelope(
                        envelope,
                        context.RequestId,
                        context.ProtocolCapability,
                        commandResult.CommandId);
                    return;
                }

                var doc = JsonDocument.Parse(jsonLine);
                var msg = new Dictionary<string, object?> { ["type"] = WsProtocol.MsgCommandResult };
                foreach (var prop in doc.RootElement.EnumerateObject())
                    msg[prop.Name] = JsonSerializer.Deserialize<JsonElement>(prop.Value.GetRawText());
                EnqueueRaw(JsonSerializer.Serialize(msg, JsonOpts));
            }
            catch (Exception ex) { _monitor.Log($"WsBridge WriteV2Result: {ex.Message}", LogLevel.Warn); }
        }

        private static Envelope BuildProtocolResultEnvelope(
            string requestId,
            string sessionId,
            CommandResult result)
        {
            return ControlFramingCodec.BuildResultResponse(requestId, sessionId, result);
        }

        private void EnqueueProtocolEnvelope(
            Envelope envelope,
            string requestId,
            string capability,
            string commandId)
        {
            var payload = Convert.ToBase64String(envelope.ToByteArray());
            var msg = new Dictionary<string, object?>
            {
                ["type"] = WsProtocol.MsgProtocolEnvelope,
                ["encoding"] = "base64",
                ["request_id"] = requestId,
                ["command_id"] = commandId,
                ["capability"] = capability,
                ["payload"] = payload,
            };
            EnqueueRaw(JsonSerializer.Serialize(msg, JsonOpts));
        }

        public void WriteStateAtomic(string json)
        {
            try
            {
                var state = JsonSerializer.Deserialize<JsonElement>(json);
                var msg = JsonSerializer.Serialize(new { type = WsProtocol.MsgStateSnapshot, state }, JsonOpts);
                Interlocked.Exchange(ref _pendingStateSnapshot, msg);
            }
            catch (Exception ex) { _monitor.Log($"WsBridge WriteStateAtomic: {ex.Message}", LogLevel.Warn); }
        }

        public void AppendEvent(string jsonLine)
        {
            try
            {
                var evt = JsonSerializer.Deserialize<JsonElement>(jsonLine);
                var msg = new { type = WsProtocol.MsgGameEvent, @event = evt };
                EnqueueRaw(JsonSerializer.Serialize(msg, JsonOpts));
            }
            catch (Exception ex) { _monitor.Log($"WsBridge AppendEvent: {ex.Message}", LogLevel.Warn); }
        }

        public int NextSeq() => Interlocked.Increment(ref _seq);

        public List<AgentCommand> ReadNewCommands()
        {
            var list = new List<AgentCommand>();
            while (_pendingCommands.TryDequeue(out var cmd))
                list.Add(cmd);
            return list;
        }

        public List<Envelope> ReadNewProtocolEnvelopes()
        {
            var list = new List<Envelope>();
            while (_pendingProtocolEnvelopes.TryDequeue(out var envelope))
                list.Add(envelope);
            return list;
        }

        public void WriteProtocolResult(string requestId, string sessionId, CommandResult result)
        {
            var envelope = BuildProtocolResultEnvelope(requestId, sessionId, result);
            EnqueueProtocolEnvelope(envelope, requestId, result.Capability, result.CommandId);
        }

        public void WriteProtocolStatus(string requestId, string sessionId, CommandStatusResponse response)
        {
            var request = new Envelope
            {
                RequestId = requestId, SessionId = sessionId,
                ProtocolVersion = "2.4.5", MessageId = "status-request-correlation",
                CommandStatusRequest = new CommandStatusRequest { CommandId = response.CommandId },
            };
            var envelope = ControlFramingCodec.BuildStatusResponse(request, response);
            EnqueueProtocolEnvelope(envelope, requestId, "", response.CommandId);
        }

        public void FallbackToLegacyMapper(Envelope envelope)
        {
            var request = envelope.CommandRequest;
            try
            {
                var v2Cmd = ProtoCommandMapper.ToV2Command(request, out var diagnostics);
                var protocolCapability = CapabilityNameMap.ToProtocol(v2Cmd.Capability);

                _pendingProtocolContexts[v2Cmd.ActionId] = new ProtocolPendingContext
                {
                    RequestId = envelope.RequestId,
                    SessionId = envelope.SessionId,
                    ProtocolCapability = protocolCapability,
                    Diagnostics = diagnostics,
                };

                var cmd = new AgentCommand
                {
                    ActionId = v2Cmd.ActionId,
                    Capability = v2Cmd.Capability,
                    Seq = v2Cmd.Seq,
                    ProtocolVersion = "2.0",
                    Params = JsonSerializer.SerializeToElement(v2Cmd.Params, JsonOpts),
                    TargetPlayerId = v2Cmd.TargetPlayerId,
                };
                _pendingCommands.Enqueue(cmd);
                _monitor.Log($"WsBridge proto fallback to legacy: request_id={envelope.RequestId} command_id={request.CommandId} cap={protocolCapability}->{v2Cmd.Capability} seq={v2Cmd.Seq}", LogLevel.Info);
            }
            catch (ProtocolMappingException ex)
            {
                _monitor.Log($"WsBridge proto fallback rejected: {ex.ErrorCode} {ex.Message}", LogLevel.Warn);
                var result = ProtoResultMapper.FromMappingException(request, ex);
                var response = BuildProtocolResultEnvelope(envelope.RequestId, envelope.SessionId, result);
                EnqueueProtocolEnvelope(response, envelope.RequestId, result.Capability, result.CommandId);
            }
        }
    }
}
