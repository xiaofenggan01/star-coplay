using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Google.Protobuf;
using StarCoPlay.Configuration;
using StarCoPlay.Logging;
using StarCoPlay.Models;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.Models;
using StardewModdingAPI;

namespace StarCoPlay.Bridge
{
    /// <summary>Local Direct client selected by an owner-only rendezvous file.</summary>
    public sealed class LocalDirectBridge : IBridge, IProtocolBridge
    {
        public static string RendezvousPath => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".starcoplay", "local-direct", "rendezvous.json");

        private readonly IMonitor _monitor;
        private readonly string _bridgeDir;
        private RendezvousMaterial _material = null!;
        private LocalDirectDispatcherBoundary _dispatchBoundary = null!;
        private readonly ConcurrentQueue<PendingFrame> _pending = new();
        private readonly ConcurrentQueue<PendingCancel> _pendingCancels = new();
        private readonly ConcurrentQueue<AgentControlSnapshot> _pendingControls = new();
        private readonly ConcurrentQueue<string> _outbound = new();
        private readonly ConcurrentDictionary<string, string> _wireFormats = new(StringComparer.Ordinal);
        private readonly ConcurrentDictionary<string, string> _states = new(StringComparer.Ordinal);
        private ClientWebSocket? _socket;
        private CancellationTokenSource? _cts;
        private Task? _loop;
        private int _seq;
        private volatile bool _live;

        /// <summary>
        /// 线程安全存活标记：<c>true</c> 表示内部 <see cref="RunAsync"/> 正在运行且
        /// WebSocket 连接处于健康/重连循环中；<c>false</c> 表示循环已退出（不可恢复）。
        /// 由 <see cref="Initialize"/> 设置、<see cref="RunAsync"/> finally 清除，
        /// 供 <see cref="ModEntry"/> 判断是否需要接管新 rendezvous 或回退到 WS Bridge。
        /// </summary>
        public bool IsLive => _live;

        internal static bool ShouldSelectForMode(string? bridgeMode, bool rendezvousAvailable)
        {
            if (!rendezvousAvailable)
                return false;

            var mode = bridgeMode?.Trim();
            return string.Equals(mode, "auto", StringComparison.OrdinalIgnoreCase)
                || string.Equals(mode, "local_direct", StringComparison.OrdinalIgnoreCase)
                || string.Equals(mode, "local-direct", StringComparison.OrdinalIgnoreCase);
        }

        internal static bool ShouldRebuildForLocalRendezvous(
            bool currentBridgeIsLocal,
            bool localBridgeIsLive,
            bool rendezvousAvailable) =>
            currentBridgeIsLocal
                ? !localBridgeIsLive
                : rendezvousAvailable;

        private sealed record PendingFrame(JsonElement Root, LocalDirectFence Fence,
            string CommandId, string Fingerprint, long DeadlineMs);
        private sealed record PendingCancel(LocalDirectFence Fence, string ControlId,
            string CommandId, string Fingerprint, long DeadlineMs);

        private sealed class RendezvousMaterial
        {
            public string Endpoint { get; set; } = "";
            public string Proof { get; set; } = "";
            public string ContextRef { get; set; } = "";
            public string PrincipalRef { get; set; } = "";
            public string RuntimeRef { get; set; } = "";
            public string GameSessionRef { get; set; } = "";
            public string GameInstanceNonce { get; set; } = "";
            public int Generation { get; set; }
            public string BindingRef { get; set; } = "";
            public int BindingVersion { get; set; }
            public int LeaseEpoch { get; set; }
            public string CapabilityDigest { get; set; } = "";
            public List<CapabilitySnapshotEntry> CapabilityEntries { get; set; } = new();
            public bool RequireToken { get; set; }
            public string Token { get; set; } = "";
        }

        private static readonly JsonSerializerOptions Snake = new()
        {
            PropertyNamingPolicy = new SnakeCaseNamingPolicy(),
            PropertyNameCaseInsensitive = true,
        };

        public static bool IsRendezvousAvailable() => File.Exists(RendezvousPath);

        /// <summary>
        /// Selection gate for the DSH plugin: a rendezvous published with
        /// require_token is claimable only by the process launched with the
        /// matching STARCOPLAY_LOCAL_DIRECT_TOKEN. The human's Steam instance
        /// has no token, so it stays on the WS bridge and cannot steal the route.
        /// Rendezvous files without require_token (the MCP local_direct path)
        /// keep the legacy behaviour.
        /// </summary>
        public static bool IsSelectableForThisInstance()
        {
            if (!File.Exists(RendezvousPath)) return false;
            try
            {
                using var document = JsonDocument.Parse(File.ReadAllText(RendezvousPath));
                var root = document.RootElement;
                var require = root.TryGetProperty("require_token", out var requireProp)
                    && requireProp.ValueKind == JsonValueKind.True;
                if (!require) return true;
                if (!root.TryGetProperty("token", out var tokenProp)
                    || tokenProp.ValueKind != JsonValueKind.String)
                    return false;
                var expected = tokenProp.GetString() ?? "";
                var actual = Environment.GetEnvironmentVariable("STARCOPLAY_LOCAL_DIRECT_TOKEN") ?? "";
                return expected.Length > 0 && string.Equals(expected, actual, StringComparison.Ordinal);
            }
            catch
            {
                return false;
            }
        }

        public LocalDirectBridge(IMonitor monitor, string modDir)
        {
            _monitor = monitor;
            _bridgeDir = Path.Combine(modDir, "bridge-local-direct");
        }

        private void ClaimRendezvous()
        {
            var claimPath = RendezvousPath + ".claimed-" + Guid.NewGuid().ToString("N");
            File.Move(RendezvousPath, claimPath); // same-directory atomic single-owner claim
            string json;
            try { json = File.ReadAllText(claimPath); }
            finally { File.Delete(claimPath); }
            _material = JsonSerializer.Deserialize<RendezvousMaterial>(json, Snake)
                ?? throw new InvalidDataException("local rendezvous is invalid");
            if (_material.RequireToken)
            {
                var actual = Environment.GetEnvironmentVariable("STARCOPLAY_LOCAL_DIRECT_TOKEN") ?? "";
                if (!string.Equals(_material.Token, actual, StringComparison.Ordinal))
                    throw new InvalidDataException("local direct token mismatch");
            }
            var digest = HostedRouteHello.ComputeCapabilityDigest(_material.CapabilityEntries);
            if (!string.Equals(digest, _material.CapabilityDigest, StringComparison.Ordinal))
                throw new InvalidDataException("local capability snapshot digest mismatch");
            _dispatchBoundary = new LocalDirectDispatcherBoundary(ExpectedFence());
        }

        internal LocalDirectBridge(LocalDirectFence fence)
        {
            _monitor = null!;
            _bridgeDir = "contract-test";
            _material = new RendezvousMaterial
            {
                ContextRef = fence.ContextRef, PrincipalRef = fence.PrincipalRef,
                RuntimeRef = fence.RuntimeRef, GameSessionRef = fence.GameSessionRef,
                GameInstanceNonce = fence.GameInstanceNonce, Generation = fence.Generation,
                BindingRef = fence.BindingRef, BindingVersion = fence.BindingVersion,
                LeaseEpoch = fence.LeaseEpoch, CapabilityDigest = fence.CapabilityDigest,
            };
            _dispatchBoundary = new LocalDirectDispatcherBoundary(fence);
        }

        internal void AcceptFrameForContractTest(string json) => HandleFrame(json);
        internal bool TryTakeOutboundForContractTest(out string? frame) => _outbound.TryDequeue(out frame);

        public string BridgeDir => _bridgeDir;
        public void SetLogger(ModLogger logger) { }
        public void EnsureDirectoryExists() => Directory.CreateDirectory(_bridgeDir);

        public void Initialize()
        {
            // The constructor is preparation-only. Claim the one-shot rendezvous at the
            // activation boundary so a failed dependency build can't consume it.
            if (_material == null)
                ClaimRendezvous();
            EnsureDirectoryExists();
            _live = true;
            _cts = new CancellationTokenSource();
            _loop = Task.Run(() => RunAsync(_cts.Token));
        }

        private async Task RunAsync(CancellationToken token)
        {
            string? reconnectProof = null;
            try
            {
                while (!token.IsCancellationRequested)
                {
                    _socket = new ClientWebSocket();
                    await _socket.ConnectAsync(new Uri(_material.Endpoint), token).ConfigureAwait(false);
                    var hello = new Dictionary<string, object?>
                    {
                        ["type"] = "local_direct_hello", ["profile"] = "local_direct",
                        ["generation"] = _material.Generation,
                        ["binding_ref"] = _material.BindingRef, ["binding_version"] = _material.BindingVersion,
                        ["game_session_ref"] = _material.GameSessionRef,
                        ["game_instance_nonce"] = _material.GameInstanceNonce,
                        ["lease_epoch"] = _material.LeaseEpoch,
                        ["capability_entries"] = _material.CapabilityEntries.Select(EntryJson).ToArray(),
                        ["capability_digest"] = _material.CapabilityDigest,
                        ["token"] = Environment.GetEnvironmentVariable("STARCOPLAY_LOCAL_DIRECT_TOKEN") ?? "",
                    };
                    if (reconnectProof == null) hello["proof"] = _material.Proof;
                    else hello["reconnect_proof"] = reconnectProof;
                    await SendAsync(hello, token).ConfigureAwait(false);
                    var readyText = await ReceiveTextAsync(_socket, token).ConfigureAwait(false);
                    using (var ready = JsonDocument.Parse(readyText))
                    {
                        if (ready.RootElement.GetProperty("type").GetString() != "local_direct_ready")
                            throw new InvalidDataException("local route did not acknowledge readiness");
                        reconnectProof = ready.RootElement.GetProperty("reconnect_proof").GetString();
                    }
                    _material.Proof = "";
                    // 脱敏 Info 日志：包含 generation/binding_version/lease_epoch/capability_digest，不含 secret
                    _monitor.Log(ConfigReloadCoordinator.SanitizeReadyLog(
                        _material.Generation, _material.BindingVersion,
                        _material.LeaseEpoch, _material.CapabilityDigest), LogLevel.Info);
                    try
                    {
                        await Task.WhenAll(ReceiveLoopAsync(_socket, token), SendLoopAsync(_socket, token))
                            .ConfigureAwait(false);
                    }
                    catch (WebSocketException) when (!token.IsCancellationRequested) { }
                    _socket.Dispose();
                    _socket = null;
                    await Task.Delay(200, token).ConfigureAwait(false);
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception ex) { _monitor.Log($"Local Direct bridge stopped [{ex.GetType().Name}]", LogLevel.Warn); }
            finally { _live = false; }
        }

        private async Task ReceiveLoopAsync(ClientWebSocket socket, CancellationToken token)
        {
            while (!token.IsCancellationRequested && socket.State == WebSocketState.Open)
                HandleFrame(await ReceiveTextAsync(socket, token).ConfigureAwait(false));
        }

        private async Task SendLoopAsync(ClientWebSocket socket, CancellationToken token)
        {
            while (!token.IsCancellationRequested && socket.State == WebSocketState.Open)
            {
                while (_outbound.TryDequeue(out var output))
                    await SendTextAsync(socket, output, token).ConfigureAwait(false);
                await Task.Delay(20, token).ConfigureAwait(false);
            }
        }

        private void HandleFrame(string json)
        {
            using var document = JsonDocument.Parse(json);
            var root = document.RootElement.Clone();
            var type = root.GetProperty("type").GetString();
            if (type == "local_command")
            {
                var fence = ParseFence(root.GetProperty("fence"));
                var commandId = root.GetProperty("command_id").GetString() ?? "";
                var fingerprint = root.GetProperty("payload").GetRawText();
                var deadline = (long)(root.GetProperty("deadline_at").GetDouble() * 1000);
                _pending.Enqueue(new PendingFrame(root, fence, commandId, fingerprint, deadline));
            }
            else if (type is "local_status" or "local_result")
            {
                var fence = ParseFence(root.GetProperty("fence"));
                var deadline = (long)(root.GetProperty("deadline_at").GetDouble() * 1000);
                if (!fence.Equals(ExpectedFence()) || deadline <= DateTimeOffset.UtcNow.ToUnixTimeMilliseconds())
                    return;
                var commandId = root.GetProperty("command_id").GetString() ?? "";
                var controlId = root.GetProperty("control_id").GetString() ?? "";
                if (!string.Equals(controlId, type + ":" + commandId, StringComparison.Ordinal))
                    return;
                QueueResult(commandId, _states.TryGetValue(commandId, out var state) ? state : "UNKNOWN",
                    null, state == null ? "result_not_known" : null, controlId: controlId);
            }
            else if (type == "local_control")
            {
                if (!root.TryGetProperty("fence", out var fenceProp)) return;
                var fence = ParseFence(fenceProp);
                if (!fence.Equals(ExpectedFence())) return;
                var enabled = root.TryGetProperty("enabled", out var enabledProp)
                    && enabledProp.ValueKind == JsonValueKind.True;
                string? target = root.TryGetProperty("target_player_id", out var targetProp)
                    && targetProp.ValueKind == JsonValueKind.String
                    ? targetProp.GetString() : null;
                long revision = root.TryGetProperty("revision", out var revisionProp)
                    && revisionProp.TryGetInt64(out var parsedRevision) ? parsedRevision : 0;
                try { _pendingControls.Enqueue(AgentControlSnapshot.Normalize(enabled, target, revision)); }
                catch (Exception ex) { _monitor?.Log("local_control rejected: " + ex.Message, LogLevel.Warn); }
            }
            else if (type == "local_task_board")
            {
                if (!root.TryGetProperty("fence", out var boardFence))
                    return;
                if (!ParseFence(boardFence).Equals(ExpectedFence()))
                    return;
                if (root.TryGetProperty("board", out var board))
                    TaskBoardState.Apply(board);
            }
            else if (type == "local_cancel")
            {
                var commandId = root.GetProperty("command_id").GetString() ?? "";
                var controlId = root.GetProperty("control_id").GetString() ?? "";
                if (!string.Equals(controlId, "local_cancel:" + commandId, StringComparison.Ordinal))
                    return;
                var fence = ParseFence(root.GetProperty("fence"));
                var deadline = (long)(root.GetProperty("deadline_at").GetDouble() * 1000);
                _pendingCancels.Enqueue(new PendingCancel(
                    fence, controlId, commandId, root.GetRawText(), deadline));
            }
        }

        public List<Envelope> ReadNewProtocolEnvelopes()
        {
            var result = new List<Envelope>();
            DrainCancels("protocol-2.4.5", cancel => result.Add(new Envelope
            {
                ProtocolVersion = "2.4.5", MessageId = $"local_{cancel.ControlId}",
                RequestId = cancel.ControlId, SessionId = _material.GameSessionRef,
                TimestampMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                CommandCancel = new CommandCancel { CommandId = cancel.CommandId, Reason = "local cancel" },
            }));
            Drain("protocol-2.4.5", frame =>
            {
                var request = JsonParser.Default.Parse<CommandRequest>(
                    frame.Root.GetProperty("payload").GetRawText());
                var envelope = new Envelope
                {
                    ProtocolVersion = "2.4.5", MessageId = $"local_{Guid.NewGuid():N}",
                    RequestId = frame.CommandId, SessionId = _material.GameSessionRef,
                    TimestampMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(), CommandRequest = request,
                };
                _wireFormats[frame.CommandId] = "protocol-2.4.5";
                result.Add(envelope);
            });
            return result;
        }

        public List<AgentCommand> ReadNewCommands()
        {
            var result = new List<AgentCommand>();
            DrainCancels("v2-json", cancel => result.Add(
                new AgentCommand { Action = "__cancel__", ActionId = cancel.CommandId }));
            Drain("v2-json", frame =>
            {
                var payload = frame.Root.GetProperty("payload");
                result.Add(new AgentCommand
                {
                    ActionId = frame.CommandId,
                    Capability = payload.GetProperty("capability").GetString(),
                    ProtocolVersion = "2.0",
                    Params = payload.GetProperty("params").Clone(),
                });
                _wireFormats[frame.CommandId] = "v2-json";
            });
            return result;
        }

        private void DrainCancels(string wireFormat, Action<PendingCancel> dispatch)
        {
            var deferred = new List<PendingCancel>();
            while (_pendingCancels.TryDequeue(out var cancel))
            {
                if (!_wireFormats.TryGetValue(cancel.CommandId, out var wire) || wire != wireFormat)
                {
                    deferred.Add(cancel);
                    continue;
                }
                var admission = _dispatchBoundary.AdmitAndDispatch(
                    cancel.Fence, cancel.ControlId, cancel.Fingerprint, cancel.DeadlineMs,
                    DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(), () => dispatch(cancel),
                    cached => _outbound.Enqueue(cached));
                if (admission.Admission == LocalDirectAdmission.Accepted)
                {
                    var response = QueueResult(cancel.CommandId, "RUNNING", null, null,
                        accepted: true, controlId: cancel.ControlId);
                    _dispatchBoundary.Complete(ExpectedFence(), cancel.ControlId, response);
                }
                else if (admission.Admission != LocalDirectAdmission.Duplicate)
                    QueueResult(cancel.CommandId, "UNKNOWN", null, admission.Admission.ToString(),
                        accepted: false, controlId: cancel.ControlId);
            }
            foreach (var cancel in deferred) _pendingCancels.Enqueue(cancel);
        }

        private void Drain(string wireFormat, Action<PendingFrame> dispatch)
        {
            var deferred = new List<PendingFrame>();
            while (_pending.TryDequeue(out var frame))
            {
                if (frame.Root.GetProperty("wire_format").GetString() != wireFormat)
                {
                    deferred.Add(frame);
                    continue;
                }
                var admission = _dispatchBoundary.AdmitAndDispatch(
                    frame.Fence, frame.CommandId, frame.Fingerprint, frame.DeadlineMs,
                    DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                    () => dispatch(frame), cached => _outbound.Enqueue(cached));
                if (admission.Admission == LocalDirectAdmission.Accepted)
                {
                    _states[frame.CommandId] = "ACCEPTED";
                    QueueResult(frame.CommandId, "ACCEPTED", null, null);
                    continue;
                }
                if (admission.Admission != LocalDirectAdmission.Duplicate)
                    QueueResult(frame.CommandId, "FAILED", null, admission.Admission.ToString());
            }
            foreach (var frame in deferred) _pending.Enqueue(frame);
        }

        public void WriteProtocolResult(string requestId, string sessionId, CommandResult result)
        {
            var state = result.Status.ToString().ToUpperInvariant() switch
            {
                "COMPLETED" => "SUCCEEDED", "CANCELLED" => "CANCELLED",
                "TIMEOUT" => "TIMED_OUT", _ => "FAILED",
            };
            QueueResult(result.CommandId, state,
                JsonFormatter.Default.Format(result), result.Error?.Code.ToString());
        }

        public void WriteProtocolStatus(string requestId, string sessionId, CommandStatusResponse response)
        {
            QueueResult(response.CommandId,
                response.Terminal ? response.Status.ToString().ToUpperInvariant() : "RUNNING",
                JsonFormatter.Default.Format(response), null, controlId: requestId);
        }

        public void WriteV2Result(string jsonLine)
        {
            using var doc = JsonDocument.Parse(jsonLine);
            var root = doc.RootElement;
            var commandId = root.TryGetProperty("action_id", out var id) ? id.GetString() ?? "" : "";
            var success = root.TryGetProperty("success", out var ok) && ok.GetBoolean();
            string? errorCode = null;
            if (!success && root.TryGetProperty("error", out var error)
                && error.ValueKind == JsonValueKind.Object
                && error.TryGetProperty("code", out var code)
                && code.ValueKind == JsonValueKind.String)
                errorCode = code.GetString();

            var state = success ? "SUCCEEDED" : errorCode switch
            {
                "CANCELLED" => "CANCELLED",
                "TIMEOUT" => "TIMED_OUT",
                not null when errorCode.EndsWith("_TIMEOUT", StringComparison.Ordinal) => "TIMED_OUT",
                _ => "FAILED",
            };
            QueueResult(commandId, state, jsonLine,
                success ? null : string.IsNullOrWhiteSpace(errorCode) ? "execution_failed" : errorCode);
        }

        /// <summary>Drain control snapshots on the game thread.</summary>
        public bool TryTakeControl(out AgentControlSnapshot snapshot) =>
            _pendingControls.TryDequeue(out snapshot);

        /// <summary>Publish this instance's player identity to the local plugin.</summary>
        public void WriteIdentity(ulong playerUniqueId, string playerName, bool isAiControlled,
            string? targetPlayerId, long revision)
        {
            var frame = JsonSerializer.Serialize(new Dictionary<string, object?>
            {
                ["type"] = "local_identity",
                ["fence"] = FenceJson(),
                ["player_unique_id"] = playerUniqueId.ToString(),
                ["player_name"] = playerName,
                ["is_ai_controlled"] = isAiControlled,
                ["target_player_id"] = targetPlayerId,
                ["revision"] = revision,
            });
            _outbound.Enqueue(frame);
        }

        private Dictionary<string, object?> FenceJson()
        {
            var fence = ExpectedFence();
            return new Dictionary<string, object?>
            {
                ["context_ref"] = fence.ContextRef,
                ["principal_ref"] = fence.PrincipalRef,
                ["runtime_ref"] = fence.RuntimeRef,
                ["game_session_ref"] = fence.GameSessionRef,
                ["game_instance_nonce"] = fence.GameInstanceNonce,
                ["generation"] = fence.Generation,
                ["binding_ref"] = fence.BindingRef,
                ["binding_version"] = fence.BindingVersion,
                ["lease_epoch"] = fence.LeaseEpoch,
                ["capability_digest"] = fence.CapabilityDigest,
            };
        }

        private string QueueResult(string commandId, string state, string? output, string? error,
            bool? accepted = null, string? controlId = null)
        {
            object? structuredOutput = null;
            if (!string.IsNullOrWhiteSpace(output))
            {
                using var outputDocument = JsonDocument.Parse(output);
                if (outputDocument.RootElement.ValueKind != JsonValueKind.Object)
                    throw new InvalidDataException("local command output must be a JSON object");
                structuredOutput = outputDocument.RootElement.Clone();
            }
            var frame = JsonSerializer.Serialize(new Dictionary<string, object?>
            {
                ["type"] = "local_command_result", ["command_id"] = commandId,
                ["state"] = state, ["output"] = structuredOutput, ["error_code"] = error,
                ["accepted"] = accepted,
                ["control_id"] = controlId,
            });
            _states[commandId] = state;
            if (state is "SUCCEEDED" or "FAILED" or "CANCELLED" or "TIMED_OUT")
                _dispatchBoundary.Complete(ExpectedFence(), commandId, frame);
            _outbound.Enqueue(frame);
            return frame;
        }

        public void Shutdown()
        {
            _cts?.Cancel();
            try { _loop?.Wait(1000); } catch { }
            try { _socket?.Abort(); _socket?.Dispose(); } catch { }
            _live = false;
        }

        public void WriteAdapterInfo(string json) { }
        public void WriteStateAtomic(string json) { }

        /// <summary>
        /// Forward one already-serialized runtime event to the local plugin so the
        /// agent can react to in-game chat and other events without polling.
        /// </summary>
        public void AppendEvent(string jsonLine)
        {
            if (!_live || string.IsNullOrWhiteSpace(jsonLine)) return;
            try
            {
                using var document = JsonDocument.Parse(jsonLine);
                var frame = JsonSerializer.Serialize(new Dictionary<string, object?>
                {
                    ["type"] = "local_event",
                    ["fence"] = FenceJson(),
                    ["event"] = document.RootElement.Clone(),
                });
                _outbound.Enqueue(frame);
            }
            catch (JsonException) { }
        }
        public int NextSeq() => Interlocked.Increment(ref _seq);

        private LocalDirectFence ExpectedFence() => new(
            _material.ContextRef, _material.PrincipalRef, _material.RuntimeRef,
            _material.GameSessionRef, _material.GameInstanceNonce, _material.Generation,
            _material.BindingRef, _material.BindingVersion, _material.LeaseEpoch,
            _material.CapabilityDigest);

        private static LocalDirectFence ParseFence(JsonElement value) => new(
            value.GetProperty("context_ref").GetString() ?? "",
            value.GetProperty("principal_ref").GetString() ?? "",
            value.GetProperty("runtime_ref").GetString() ?? "",
            value.GetProperty("game_session_ref").GetString() ?? "",
            value.GetProperty("game_instance_nonce").GetString() ?? "",
            value.GetProperty("generation").GetInt32(),
            value.GetProperty("binding_ref").GetString() ?? "",
            value.GetProperty("binding_version").GetInt32(),
            value.GetProperty("lease_epoch").GetInt32(),
            value.GetProperty("capability_digest").GetString() ?? "");

        private static Dictionary<string, string> EntryJson(CapabilitySnapshotEntry entry) => new()
        {
            ["source_surface"] = entry.SourceSurface, ["capability_id"] = entry.CapabilityId,
            ["capability_contract_version"] = entry.CapabilityContractVersion,
        };

        private async Task SendAsync(Dictionary<string, object?> frame, CancellationToken token) =>
            await SendTextAsync(_socket!, JsonSerializer.Serialize(frame), token).ConfigureAwait(false);

        private static async Task SendTextAsync(ClientWebSocket socket, string text, CancellationToken token)
        {
            var bytes = Encoding.UTF8.GetBytes(text);
            await socket.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, token).ConfigureAwait(false);
        }

        private static async Task<string> ReceiveTextAsync(ClientWebSocket socket, CancellationToken token)
        {
            var buffer = new byte[64 * 1024];
            var result = await socket.ReceiveAsync(new ArraySegment<byte>(buffer), token).ConfigureAwait(false);
            if (result.MessageType == WebSocketMessageType.Close) throw new WebSocketException("closed");
            return Encoding.UTF8.GetString(buffer, 0, result.Count);
        }
    }
}
