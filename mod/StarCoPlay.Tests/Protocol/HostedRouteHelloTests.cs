using System;
using System.Linq;
using NUnit.Framework;
using StarCoPlay.Bridge;

namespace StarCoPlay.Tests.Protocol;

[TestFixture]
public sealed class HostedRouteHelloTests
{
    [Test]
    public void HostedContractVersion_MatchesGatewayAuthorityContract()
    {
        Assert.That(HostedRouteHello.ContractVersion, Is.EqualTo("starcoplay.hosted-route.v1"));
    }

    [Test]
    public void SnapshotVersion_MatchesCrossLanguageAlgorithm()
    {
        Assert.That(HostedRouteHello.SnapshotVersion, Is.EqualTo("starcoplay.capability-snapshot.v1"));
    }

    [Test]
    public void EmptyOrMissingFields_FailClosed()
    {
        var entry = new CapabilitySnapshotEntry("protocol-2.4.5", "say", "2.4.5");
        Assert.Throws<ArgumentException>(() => new HostedRouteHello("", new[] { entry }));
        Assert.Throws<ArgumentException>(() => new HostedRouteHello("nonce", Array.Empty<CapabilitySnapshotEntry>()));
        Assert.Throws<ArgumentException>(() => HostedRouteHello.ComputeCapabilityDigest(Array.Empty<CapabilitySnapshotEntry>()));

        // Each field individually empty
        Assert.Throws<ArgumentException>(() => HostedRouteHello.CanonicalEntries(new[]
        {
            new CapabilitySnapshotEntry("", "say", "2.4.5"),
        }));
        Assert.Throws<ArgumentException>(() => HostedRouteHello.CanonicalEntries(new[]
        {
            new CapabilitySnapshotEntry("protocol-2.4.5", "", "2.4.5"),
        }));
        Assert.Throws<ArgumentException>(() => HostedRouteHello.CanonicalEntries(new[]
        {
            new CapabilitySnapshotEntry("protocol-2.4.5", "say", ""),
        }));
    }

    [Test]
    public void CanonicalEntries_RemovesDuplicatesAndSortsDeterministically()
    {
        var a = new CapabilitySnapshotEntry("protocol-2.4.5", "say", "2.4.5");
        var b = new CapabilitySnapshotEntry("protocol-2.4.5", "face", "2.4.5");
        var c = new CapabilitySnapshotEntry("protocol-2.4.5", "emote", "2.4.5");

        var canonical = HostedRouteHello.CanonicalEntries(new[] { b, a, c, a, b });
        Assert.That(canonical.Count, Is.EqualTo(3));
        // Sorted by the tab-separated line representation
        Assert.That(canonical[0].CapabilityId, Is.EqualTo("emote"));
        Assert.That(canonical[1].CapabilityId, Is.EqualTo("face"));
        Assert.That(canonical[2].CapabilityId, Is.EqualTo("say"));
    }

    [Test]
    public void CapabilityDigest_MatchesPythonCrossLanguageAgreementVector()
    {
        // Cross-language canonical: Python compute_capability_digest produces the same value.
        // Both use: sha256("starcoplay.capability-snapshot.v1\n" + sorted unique lines)
        // where each line is "source_surface\tcapability_id\tcontract_version\n".
        var entries = new[]
        {
            new CapabilitySnapshotEntry("protocol-2.4.5", "face", "2.4.5"),
            new CapabilitySnapshotEntry("protocol-2.4.5", "say", "2.4.5"),
        };
        var digest = HostedRouteHello.ComputeCapabilityDigest(entries);

        Assert.That(digest, Is.EqualTo(
            "sha256:f9daca8a6fd4072c95bcd36cec6c8c0c67cab546c3fc1ceaba0df0987db29778"));
    }

    [Test]
    public void SingleEntry_ProducesValidDigest()
    {
        var entry = new CapabilitySnapshotEntry("protocol-2.4.5", "say", "2.4.5");
        var digest = HostedRouteHello.ComputeCapabilityDigest(new[] { entry });
        Assert.That(digest, Does.StartWith("sha256:"));
        Assert.That(digest, Has.Length.EqualTo(71));
    }

    [Test]
    public void ProductionEntries_PublishRegisteredV2AllowlistOnly()
    {
        var entries = HostedRouteHello.ProductionCapabilityEntries(
            new[] { "say", "face" },
            new[] { "water_crops", "go_to", "dev_eval", "test_input" });

        Assert.That(entries, Does.Contain(
            new CapabilitySnapshotEntry("protocol-2.4.5", "say", "2.4.5")));
        Assert.That(entries, Does.Contain(
            new CapabilitySnapshotEntry("v2-compat", "water_crops", "2.4.5")));
        Assert.That(entries, Does.Contain(
            new CapabilitySnapshotEntry("v2-compat", "navigate_to_target", "2.4.5")));
        Assert.That(entries.Any(entry => entry.CapabilityId == "dev_eval"), Is.False);
        Assert.That(entries.Any(entry => entry.CapabilityId == "test_input"), Is.False);
    }
}
