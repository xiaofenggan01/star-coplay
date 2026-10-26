using System;
using NUnit.Framework;
using StarCoPlay.Bridge;

namespace StarCoPlay.Tests.Protocol;

[TestFixture]
public sealed class WsReconnectBackoffTests
{
    [Test]
    public void ImmediatePostAuthenticationFailureKeepsBackoffBudget()
    {
        Assert.That(
            WsBridge.ShouldResetReconnectBackoff(true, TimeSpan.FromSeconds(1)),
            Is.False);
    }

    [Test]
    public void StableAuthenticatedConnectionResetsBackoffBudget()
    {
        Assert.That(
            WsBridge.ShouldResetReconnectBackoff(true, TimeSpan.FromSeconds(30)),
            Is.True);
    }

    [Test]
    public void UnauthenticatedConnectionNeverResetsBackoffBudget()
    {
        Assert.That(
            WsBridge.ShouldResetReconnectBackoff(false, TimeSpan.FromMinutes(1)),
            Is.False);
    }
}
