using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

namespace StarCoPlay.Bridge
{
    public readonly record struct CapabilitySnapshotEntry(
        string SourceSurface,
        string CapabilityId,
        string CapabilityContractVersion);

    /// <summary>Non-authoritative Mod proof submitted during Hosted hello.</summary>
    public sealed class HostedRouteHello
    {
        public const string ContractVersion = "starcoplay.hosted-route.v1";
        public const string SnapshotVersion = "starcoplay.capability-snapshot.v1";

        public string GameInstanceNonce { get; }
        public IReadOnlyList<CapabilitySnapshotEntry> CapabilityEntries { get; }
        public string CapabilityDigest { get; }

        private static readonly (string CanonicalId, string WireId)[] ProductionV2Capabilities =
        {
            ("water_crops", "water_crops"),
            ("harvest_crops", "harvest_crops"),
            ("ship_items", "ship_items"),
            ("buy_items", "buy_items"),
            ("plant_crops", "plant_crops"),
            ("chop_trees", "chop_trees"),
            ("mine_rocks", "mine_rocks"),
            ("execute_on_targets", "execute_on_targets"),
            ("menu_interact", "menu_interact"),
            ("navigate_to_target", "go_to"),
            ("act_on_target", "do"),
            ("move_to_target", "move_to"),
            ("use_tool_at_target", "use_tool"),
            ("equip_matching_item", "equip"),
            ("observe_current_view", "observe"),
            ("sleep_for_night", "go_bed"),
        };

        public HostedRouteHello(string gameInstanceNonce, IEnumerable<CapabilitySnapshotEntry> entries)
        {
            if (string.IsNullOrWhiteSpace(gameInstanceNonce))
                throw new ArgumentException("game instance nonce is required", nameof(gameInstanceNonce));
            GameInstanceNonce = gameInstanceNonce;
            CapabilityEntries = CanonicalEntries(entries);
            CapabilityDigest = ComputeCapabilityDigest(CapabilityEntries);
        }

        public static IReadOnlyList<CapabilitySnapshotEntry> ProductionCapabilityEntries(
            IEnumerable<string> registeredProtoCapabilities,
            IEnumerable<string> registeredV2Capabilities)
        {
            if (registeredProtoCapabilities == null)
                throw new ArgumentNullException(nameof(registeredProtoCapabilities));
            if (registeredV2Capabilities == null)
                throw new ArgumentNullException(nameof(registeredV2Capabilities));

            var registeredV2 = new HashSet<string>(registeredV2Capabilities, StringComparer.Ordinal);
            var entries = registeredProtoCapabilities.Select(capability =>
                new CapabilitySnapshotEntry("protocol-2.4.5", capability, "2.4.5"));
            var v2Entries = ProductionV2Capabilities
                .Where(capability => registeredV2.Contains(capability.WireId))
                .Select(capability =>
                    new CapabilitySnapshotEntry("v2-compat", capability.CanonicalId, "2.4.5"));
            return CanonicalEntries(entries.Concat(v2Entries));
        }

        public static IReadOnlyList<CapabilitySnapshotEntry> CanonicalEntries(
            IEnumerable<CapabilitySnapshotEntry> entries)
        {
            if (entries == null) throw new ArgumentNullException(nameof(entries));
            var canonical = entries
                .Where(entry => !string.IsNullOrWhiteSpace(entry.SourceSurface)
                    && !string.IsNullOrWhiteSpace(entry.CapabilityId)
                    && !string.IsNullOrWhiteSpace(entry.CapabilityContractVersion))
                .Distinct()
                .OrderBy(ToLine, StringComparer.Ordinal)
                .ToArray();
            if (canonical.Length == 0)
                throw new ArgumentException("at least one registered capability is required", nameof(entries));
            return canonical;
        }

        public static string ComputeCapabilityDigest(IEnumerable<CapabilitySnapshotEntry> entries)
        {
            var lines = CanonicalEntries(entries).Select(ToLine);
            var payload = SnapshotVersion + "\n" + string.Join("", lines);
            using var sha256 = SHA256.Create();
            return "sha256:" + Convert.ToHexString(sha256.ComputeHash(Encoding.UTF8.GetBytes(payload)))
                .ToLowerInvariant();
        }

        private static string ToLine(CapabilitySnapshotEntry entry) =>
            $"{entry.SourceSurface}\t{entry.CapabilityId}\t{entry.CapabilityContractVersion}\n";
    }
}
