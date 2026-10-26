using System;
using System.Collections.Generic;

namespace StarCoPlay.Bridge
{
    public readonly record struct LocalDirectFence(
        string ContextRef,
        string PrincipalRef,
        string RuntimeRef,
        string GameSessionRef,
        string GameInstanceNonce,
        int Generation,
        string BindingRef,
        int BindingVersion,
        int LeaseEpoch,
        string CapabilityDigest);

    public enum LocalDirectAdmission
    {
        Accepted,
        Duplicate,
        Collision,
        FenceRejected,
        DeadlineExpired,
        CapacityExceeded,
    }

    public readonly record struct LocalDirectAdmissionResult(
        LocalDirectAdmission Admission,
        string? TerminalResult = null);

    /// <summary>
    /// Final synchronous guard immediately before Proto/V2 dispatch. This
    /// class deliberately performs no side effect itself.
    /// </summary>
    public sealed class LocalDirectFenceGate
    {
        private readonly LocalDirectFence _expected;
        private readonly int _capacity;
        private readonly Dictionary<string, CacheEntry> _cache = new(StringComparer.Ordinal);
        private readonly Queue<string> _insertionOrder = new();
        private readonly object _sync = new();

        private sealed class CacheEntry
        {
            public string Fingerprint { get; }
            public string? TerminalResult { get; set; }
            public CacheEntry(string fingerprint) => Fingerprint = fingerprint;
        }

        public LocalDirectFenceGate(LocalDirectFence expected, int capacity = 512)
        {
            if (capacity < 1) throw new ArgumentOutOfRangeException(nameof(capacity));
            _expected = expected;
            _capacity = capacity;
        }

        public LocalDirectAdmissionResult Admit(
            LocalDirectFence presented,
            string commandId,
            string payloadFingerprint,
            long deadlineUnixMilliseconds,
            long nowUnixMilliseconds)
        {
            if (!presented.Equals(_expected))
                return new(LocalDirectAdmission.FenceRejected);
            if (deadlineUnixMilliseconds <= nowUnixMilliseconds)
                return new(LocalDirectAdmission.DeadlineExpired);
            if (string.IsNullOrWhiteSpace(commandId) || string.IsNullOrWhiteSpace(payloadFingerprint))
                return new(LocalDirectAdmission.Collision);

            var key = presented.GameInstanceNonce + "\n" + commandId;
            lock (_sync)
            {
                if (_cache.TryGetValue(key, out var existing))
                {
                    if (!string.Equals(existing.Fingerprint, payloadFingerprint, StringComparison.Ordinal))
                        return new(LocalDirectAdmission.Collision);
                    return new(LocalDirectAdmission.Duplicate, existing.TerminalResult);
                }
                var evictionCandidates = _insertionOrder.Count;
                while (_cache.Count >= _capacity && evictionCandidates-- > 0
                    && _insertionOrder.Count > 0)
                {
                    var candidate = _insertionOrder.Dequeue();
                    if (_cache.TryGetValue(candidate, out var candidateEntry)
                        && candidateEntry.TerminalResult != null)
                    {
                        _cache.Remove(candidate);
                        break;
                    }
                    _insertionOrder.Enqueue(candidate);
                }
                if (_cache.Count >= _capacity)
                    return new(LocalDirectAdmission.CapacityExceeded);
                _cache[key] = new CacheEntry(payloadFingerprint);
                _insertionOrder.Enqueue(key);
                return new(LocalDirectAdmission.Accepted);
            }
        }

        public bool Complete(LocalDirectFence presented, string commandId, string terminalResult)
        {
            var key = presented.GameInstanceNonce + "\n" + commandId;
            lock (_sync)
            {
                if (!presented.Equals(_expected) || !_cache.TryGetValue(key, out var entry))
                    return false;
                entry.TerminalResult = terminalResult;
                return true;
            }
        }
    }

    /// <summary>
    /// The bridge-to-dispatcher boundary. The dispatcher callback is
    /// unreachable unless the final fence admission is Accepted.
    /// </summary>
    public sealed class LocalDirectDispatcherBoundary
    {
        private readonly LocalDirectFenceGate _gate;

        public LocalDirectDispatcherBoundary(LocalDirectFence expected, int capacity = 512) =>
            _gate = new LocalDirectFenceGate(expected, capacity);

        public LocalDirectAdmissionResult AdmitAndDispatch(
            LocalDirectFence fence,
            string commandId,
            string payloadFingerprint,
            long deadlineUnixMilliseconds,
            long nowUnixMilliseconds,
            Action dispatch,
            Action<string> returnCachedTerminal)
        {
            var result = _gate.Admit(fence, commandId, payloadFingerprint,
                deadlineUnixMilliseconds, nowUnixMilliseconds);
            if (result.Admission == LocalDirectAdmission.Accepted)
                dispatch();
            else if (result.Admission == LocalDirectAdmission.Duplicate
                && result.TerminalResult != null)
                returnCachedTerminal(result.TerminalResult);
            return result;
        }

        public bool Complete(LocalDirectFence fence, string commandId, string terminalResult) =>
            _gate.Complete(fence, commandId, terminalResult);
    }
}
