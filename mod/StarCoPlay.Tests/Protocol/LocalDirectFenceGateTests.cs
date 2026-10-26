using NUnit.Framework;
using StarCoPlay.Bridge;

namespace StarCoPlay.Tests.Protocol;

[TestFixture]
public sealed class LocalDirectFenceGateTests
{
    private static LocalDirectFence Fence(string nonce = "nonce") => new(
        "ctx", "principal", "runtime", "game", nonce, 1, "binding", 1, 1, "digest");

    [Test]
    public void StaleFenceAndExpiredDeadline_AreRejectedBeforeAdmission()
    {
        var gate = new LocalDirectFenceGate(Fence());
        Assert.That(gate.Admit(Fence("stale"), "cmd", "payload", 101, 100).Admission,
            Is.EqualTo(LocalDirectAdmission.FenceRejected));
        Assert.That(gate.Admit(Fence(), "cmd", "payload", 100, 100).Admission,
            Is.EqualTo(LocalDirectAdmission.DeadlineExpired));
    }

    [Test]
    public void DuplicateReturnsTerminalAndCollisionFailsClosed()
    {
        var fence = Fence();
        var gate = new LocalDirectFenceGate(fence);
        Assert.That(gate.Admit(fence, "cmd", "payload-a", 200, 100).Admission,
            Is.EqualTo(LocalDirectAdmission.Accepted));
        Assert.That(gate.Complete(fence, "cmd", "terminal-json"), Is.True);
        var duplicate = gate.Admit(fence, "cmd", "payload-a", 200, 100);
        Assert.That(duplicate.Admission, Is.EqualTo(LocalDirectAdmission.Duplicate));
        Assert.That(duplicate.TerminalResult, Is.EqualTo("terminal-json"));
        Assert.That(gate.Admit(fence, "cmd", "payload-b", 200, 100).Admission,
            Is.EqualTo(LocalDirectAdmission.Collision));
    }

    [Test]
    public void CacheNeverEvictsRunningCommandAndFailsClosedAtCapacity()
    {
        var fence = Fence();
        var gate = new LocalDirectFenceGate(fence, capacity: 2);
        Assert.That(gate.Admit(fence, "old", "a", 200, 100).Admission,
            Is.EqualTo(LocalDirectAdmission.Accepted));
        Assert.That(gate.Admit(fence, "running", "r", 200, 100).Admission,
            Is.EqualTo(LocalDirectAdmission.Accepted));
        for (var i = 0; i < 100; i++)
            Assert.That(gate.Admit(fence, "new-" + i, "b", 200, 100).Admission,
                Is.EqualTo(LocalDirectAdmission.CapacityExceeded));
        Assert.That(gate.Admit(fence, "old", "a", 200, 100).Admission,
            Is.EqualTo(LocalDirectAdmission.Duplicate));
        Assert.That(gate.Admit(fence, "running", "r", 200, 100).Admission,
            Is.EqualTo(LocalDirectAdmission.Duplicate));
    }

    [Test]
    public void BridgeDispatcherBoundary_EmitsOnlyAcceptedAndReturnsOnlyCachedTerminal()
    {
        var fence = Fence();
        var boundary = new LocalDirectDispatcherBoundary(fence);
        var dispatched = 0;
        string? cached = null;

        var accepted = boundary.AdmitAndDispatch(fence, "cmd", "payload", 200, 100,
            () => dispatched++, value => cached = value);
        Assert.That(accepted.Admission, Is.EqualTo(LocalDirectAdmission.Accepted));
        Assert.That(dispatched, Is.EqualTo(1));

        var nonterminalDuplicate = boundary.AdmitAndDispatch(fence, "cmd", "payload", 200, 100,
            () => dispatched++, value => cached = value);
        Assert.That(nonterminalDuplicate.Admission, Is.EqualTo(LocalDirectAdmission.Duplicate));
        Assert.That(dispatched, Is.EqualTo(1));
        Assert.That(cached, Is.Null);

        Assert.That(boundary.Complete(fence, "cmd", "terminal"), Is.True);
        boundary.AdmitAndDispatch(fence, "cmd", "payload", 200, 100,
            () => dispatched++, value => cached = value);
        Assert.That(dispatched, Is.EqualTo(1));
        Assert.That(cached, Is.EqualTo("terminal"));

        foreach (var rejected in new[]
        {
            boundary.AdmitAndDispatch(Fence("stale"), "stale", "x", 200, 100,
                () => dispatched++, _ => { }),
            boundary.AdmitAndDispatch(fence, "expired", "x", 100, 100,
                () => dispatched++, _ => { }),
            boundary.AdmitAndDispatch(fence, "cmd", "collision", 200, 100,
                () => dispatched++, _ => { }),
        })
            Assert.That(rejected.Admission, Is.Not.EqualTo(LocalDirectAdmission.Accepted));
        Assert.That(dispatched, Is.EqualTo(1));
    }
}
