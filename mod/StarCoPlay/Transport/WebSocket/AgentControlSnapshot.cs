using System;
using System.Globalization;
using Newtonsoft.Json;

namespace StarCoPlay.Bridge
{
    public enum AgentControlSnapshotDecision
    {
        Applied,
        Idempotent,
        Stale,
        Conflict,
    }

    /// <summary>由所有 Mod 实例共享并随 Hosted hello 提交的 owner desired-control 快照。</summary>
    public readonly record struct AgentControlSnapshot(bool Enabled, string? TargetPlayerId, long Revision)
    {
        public static AgentControlSnapshot FromConfig(ModConfig config)
        {
            if (config == null) throw new ArgumentNullException(nameof(config));
            return Normalize(config.EnableAIControl, config.TargetPlayerId, config.AgentControlRevision);
        }

        public static AgentControlSnapshot Normalize(bool enabled, string? targetPlayerId, long revision)
        {
            if (revision < 0) throw new ArgumentOutOfRangeException(nameof(revision));
            var target = string.IsNullOrWhiteSpace(targetPlayerId) ? null : targetPlayerId.Trim();
            if (target != null && (!ulong.TryParse(target, NumberStyles.None, CultureInfo.InvariantCulture, out var parsed)
                || parsed.ToString(CultureInfo.InvariantCulture) != target))
                throw new ArgumentException("target player id must be a canonical decimal string", nameof(targetPlayerId));
            if (enabled && target == null)
                throw new ArgumentException("enabled control snapshot requires a target player id", nameof(targetPlayerId));
            if (!enabled)
                target = null;
            return new AgentControlSnapshot(enabled, target, revision);
        }

        public AgentControlSnapshotDecision CompareIncoming(AgentControlSnapshot incoming)
        {
            if (incoming.Revision > Revision) return AgentControlSnapshotDecision.Applied;
            if (incoming.Revision < Revision) return AgentControlSnapshotDecision.Stale;
            return incoming.Enabled == Enabled && incoming.TargetPlayerId == TargetPlayerId
                ? AgentControlSnapshotDecision.Idempotent
                : AgentControlSnapshotDecision.Conflict;
        }

        /// <summary>
        /// Rebase a stale desired-control snapshot only on the Mod instance that
        /// actually hosts the configured target player. This lets an upgraded
        /// client recover from a higher durable Gateway revision without letting
        /// another observer connection steal the controller lease.
        /// </summary>
        public bool TryRebaseControllerClaim(
            ulong localPlayerId,
            string? connectionRole,
            long acceptedRevision,
            bool recoveryAllowed,
            out AgentControlSnapshot rebased)
        {
            rebased = this;
            if (!Enabled
                || !recoveryAllowed
                || !string.Equals(connectionRole, "observer", StringComparison.OrdinalIgnoreCase)
                || acceptedRevision < Revision
                || acceptedRevision == long.MaxValue)
                return false;

            var localPlayer = localPlayerId.ToString(CultureInfo.InvariantCulture);
            if (!string.Equals(TargetPlayerId, localPlayer, StringComparison.Ordinal))
                return false;

            rebased = Normalize(true, TargetPlayerId, checked(Math.Max(Revision, acceptedRevision) + 1));
            return true;
        }

        public AgentControlSnapshot AdvanceAfter(long acceptedRevision)
        {
            var baseline = Math.Max(Revision, acceptedRevision);
            if (baseline == long.MaxValue)
                throw new OverflowException("agent control revision exhausted");
            return Normalize(Enabled, TargetPlayerId, checked(baseline + 1));
        }

        public void ApplyTo(ModConfig config)
        {
            config.EnableAIControl = Enabled;
            config.TargetPlayerId = TargetPlayerId;
            config.AgentControlRevision = Revision;
        }
    }

    public sealed class AgentControlSnapshotMessage
    {
        [JsonProperty("action")]
        public string Action { get; set; } = "control_snapshot";
        [JsonProperty("enabled")]
        public bool Enabled { get; set; }
        [JsonProperty("target_player_id")]
        public string? TargetPlayerId { get; set; }
        [JsonProperty("revision")]
        public long Revision { get; set; }

        public AgentControlSnapshot ToSnapshot() =>
            AgentControlSnapshot.Normalize(Enabled, TargetPlayerId, Revision);
    }
}
