using System;
using NUnit.Framework;
using StarCoPlay.Bridge;

namespace StarCoPlay.Tests.Protocol;

[TestFixture]
public sealed class AgentControlSnapshotTests
{
    [Test]
    public void Normalize_RequiresCanonicalDecimalTargetWhenEnabled()
    {
        Assert.Throws<ArgumentException>(() => AgentControlSnapshot.Normalize(true, null, 1));
        Assert.Throws<ArgumentException>(() => AgentControlSnapshot.Normalize(true, "01", 1));
        Assert.Throws<ArgumentException>(() => AgentControlSnapshot.Normalize(true, "-1", 1));
        Assert.That(AgentControlSnapshot.Normalize(true, "123", 1).TargetPlayerId, Is.EqualTo("123"));
    }

    [Test]
    public void DisabledSnapshot_CanonicalizesTargetToNull()
    {
        var snapshot = AgentControlSnapshot.Normalize(false, "123", 2);
        Assert.That(snapshot.TargetPlayerId, Is.Null);
    }

    [Test]
    public void CompareIncoming_OnlyAppliesHigherRevision()
    {
        var current = AgentControlSnapshot.Normalize(true, "1", 4);
        Assert.That(current.CompareIncoming(AgentControlSnapshot.Normalize(true, "2", 5)),
            Is.EqualTo(AgentControlSnapshotDecision.Applied));
        Assert.That(current.CompareIncoming(AgentControlSnapshot.Normalize(true, "1", 3)),
            Is.EqualTo(AgentControlSnapshotDecision.Stale));
        Assert.That(current.CompareIncoming(AgentControlSnapshot.Normalize(true, "1", 4)),
            Is.EqualTo(AgentControlSnapshotDecision.Idempotent));
        Assert.That(current.CompareIncoming(AgentControlSnapshot.Normalize(false, null, 4)),
            Is.EqualTo(AgentControlSnapshotDecision.Conflict));
    }

    [Test]
    public void ApplyTo_PersistsAllSnapshotFields()
    {
        var config = new ModConfig();
        AgentControlSnapshot.Normalize(true, "123", 7).ApplyTo(config);
        Assert.Multiple(() =>
        {
            Assert.That(config.EnableAIControl, Is.True);
            Assert.That(config.TargetPlayerId, Is.EqualTo("123"));
            Assert.That(config.AgentControlRevision, Is.EqualTo(7));
        });
    }

    [Test]
    public void TryRebaseControllerClaim_OnlyRebasesTheConfiguredTargetInstance()
    {
        var stale = AgentControlSnapshot.Normalize(true, "42", 0);

        Assert.That(
            stale.TryRebaseControllerClaim(42, "observer", 6001, recoveryAllowed: true, out var rebased),
            Is.True);
        Assert.That(rebased, Is.EqualTo(AgentControlSnapshot.Normalize(true, "42", 6002)));

        Assert.That(
            stale.TryRebaseControllerClaim(7, "observer", 6001, recoveryAllowed: true, out _),
            Is.False,
            "a human/observer process must not steal the configured AI target lease");
        Assert.That(
            stale.TryRebaseControllerClaim(42, "controller", 6001, recoveryAllowed: true, out _),
            Is.False,
            "an established controller must not churn its revision");
        Assert.That(
            stale.TryRebaseControllerClaim(42, "observer", 0, recoveryAllowed: true, out _),
            Is.False,
            "equal revisions describe ordinary controller contention, not stale state");
        Assert.That(
            AgentControlSnapshot.Normalize(true, "42", 4)
                .TryRebaseControllerClaim(42, "observer", 6001, recoveryAllowed: true, out var recovered),
            Is.True);
        Assert.That(recovered, Is.EqualTo(AgentControlSnapshot.Normalize(true, "42", 6002)));
        Assert.That(
            stale.TryRebaseControllerClaim(42, "observer", 6001, recoveryAllowed: false, out _),
            Is.False,
            "an observer may only advance after the Gateway confirms the old transport is absent");
    }

    [Test]
    public void AdvanceAfter_UsesTheHighestKnownRevisionForAnExplicitUserChange()
    {
        var changed = AgentControlSnapshot.Normalize(true, "42", 4);

        Assert.That(
            changed.AdvanceAfter(6001),
            Is.EqualTo(AgentControlSnapshot.Normalize(true, "42", 6002)));
        Assert.That(
            changed.AdvanceAfter(2),
            Is.EqualTo(AgentControlSnapshot.Normalize(true, "42", 5)));
    }
}
