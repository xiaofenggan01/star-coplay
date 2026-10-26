using System;
using NUnit.Framework;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId;
using StarCoPlay.V2.RefId.Inspection;
using StarCoPlay.V2.RefId.Parsing;
using StarCoPlay.V2.RefId.Resolution;

namespace StarCoPlay.Tests.RefId.Inspection;

[TestFixture]
public class RefIdInspectorProjectionTests
{
    private const string TreeRef = "world_entity:tree:farm:41:12#wt_1";

    [Test]
    public void Inspect_ProjectionThrows_ExceptionBubblesUp()
    {
        var parsed = RefIdParser.Parse(TreeRef).ParsedRefId!;
        var inspector = new RefIdInspector(
            new FixedResolver(new RefIdResolutionResult(
                RefIdStatus.Resolved, parsed, "tree", "A tree.", null)),
            new ThrowingProjector());

        Assert.Throws<InvalidOperationException>(() =>
            inspector.Inspect(TreeRef));
    }

    [Test]
    public void Inspect_ProjectorReturnsFalse_ReturnsResolvedWithNoData()
    {
        var parsed = RefIdParser.Parse(TreeRef).ParsedRefId!;
        var inspector = new RefIdInspector(
            new FixedResolver(new RefIdResolutionResult(
                RefIdStatus.Resolved, parsed, "tree", "A tree.", null)),
            new NullRefFactProjector());

        var inspected = inspector.Inspect(TreeRef);

        Assert.That(inspected.Resolution.Status, Is.EqualTo(RefResolveStatus.RefResolved));
        Assert.That(inspected.DataCase, Is.EqualTo(InspectedRef.DataOneofCase.None));
    }

    [Test]
    public void Inspect_ResolvedDegraded_ProjectorReturnsFalse_StatusPreserved()
    {
        var parsed = RefIdParser.Parse(TreeRef).ParsedRefId!;
        var inspector = new RefIdInspector(
            new FixedResolver(new RefIdResolutionResult(
                RefIdStatus.ResolvedDegraded, parsed, "tree", "A tree.", null)),
            new NullRefFactProjector());

        var inspected = inspector.Inspect(TreeRef);

        Assert.That(inspected.Resolution.Status, Is.EqualTo(RefResolveStatus.RefResolvedDegraded));
        Assert.That(inspected.DataCase, Is.EqualTo(InspectedRef.DataOneofCase.None));
    }

    private sealed class FixedResolver : IRefIdResolver
    {
        private readonly RefIdResolutionResult _result;
        public FixedResolver(RefIdResolutionResult result) => _result = result;
        public RefIdResolutionResult Resolve(ParsedRefId parsedRefId) => _result;
    }

    private sealed class ThrowingProjector : IRefFactProjector
    {
        public bool TryProject(RefIdResolutionResult resolution, out InspectedRef inspectedRef)
        {
            inspectedRef = new InspectedRef();
            throw new InvalidOperationException("Simulated projection failure");
        }
    }
}
