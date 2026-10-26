using NUnit.Framework;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId;
using StarCoPlay.V2.RefId.Inspection;
using StarCoPlay.V2.RefId.Parsing;
using StarCoPlay.V2.RefId.Resolution;

namespace StarCoPlay.Tests.RefId.Inspection;

[TestFixture]
public class RefIdInspectorTests
{
    [Test]
    public void Inspect_MalformedRef_ReturnsMalformedEnvelope()
    {
        var inspector = new RefIdInspector(new UnsupportedRefIdResolver());

        var inspected = inspector.Inspect("world_entity:tree:farm:41:12");

        Assert.That(inspected.Resolution.Status, Is.EqualTo(RefResolveStatus.RefMalformedRef));
        Assert.That(inspected.Ref, Is.EqualTo("world_entity:tree:farm:41:12"));
        Assert.That(inspected.DataCase, Is.EqualTo(InspectedRef.DataOneofCase.None));
        Assert.That(inspected.DebugReason, Is.Not.Empty);
    }

    [Test]
    public void Inspect_UnsupportedValidRef_ReturnsUnsupportedEnvelope()
    {
        var inspector = new RefIdInspector(new UnsupportedRefIdResolver());

        var inspected = inspector.Inspect("world_entity:tree:farm:41:12#wt_1");

        Assert.That(inspected.Resolution.Status, Is.EqualTo(RefResolveStatus.RefUnsupported));
        Assert.That(inspected.Ref, Is.EqualTo("world_entity:tree:farm:41:12#wt_1"));
        Assert.That(inspected.RefType, Is.EqualTo("world_entity"));
        Assert.That(inspected.Type, Is.EqualTo("tree"));
        Assert.That(inspected.FactKind, Is.EqualTo("tree"));
        Assert.That(inspected.DataCase, Is.EqualTo(InspectedRef.DataOneofCase.None));
    }

    [Test]
    public void Inspect_ResolvedProjectedWorldEntity_ReturnsTypedData()
    {
        var inspector = new RefIdInspector(
            new StubResolver(new RefIdResolutionResult(
                RefIdStatus.Resolved,
                RefIdParser.Parse("world_entity:tree:farm:41:12#wt_1").ParsedRefId!,
                "tree",
                "Resolved tree.",
                null)),
            new StubProjector());

        var inspected = inspector.Inspect("world_entity:tree:farm:41:12#wt_1");

        Assert.That(inspected.Resolution.Status, Is.EqualTo(RefResolveStatus.RefResolved));
        Assert.That(inspected.DataCase, Is.EqualTo(InspectedRef.DataOneofCase.WorldEntity));
        Assert.That(inspected.WorldEntity.Type, Is.EqualTo("tree"));
        Assert.That(inspected.WorldEntity.FactKind, Is.EqualTo("tree"));
    }

    private sealed class StubResolver : IRefIdResolver
    {
        private readonly RefIdResolutionResult result;

        public StubResolver(RefIdResolutionResult result)
        {
            this.result = result;
        }

        public RefIdResolutionResult Resolve(ParsedRefId parsedRefId)
        {
            return result;
        }
    }

    private sealed class StubProjector : IRefFactProjector
    {
        public bool TryProject(RefIdResolutionResult resolution, out InspectedRef inspectedRef)
        {
            inspectedRef = new InspectedRef
            {
                WorldEntity = new WorldEntityFact
                {
                    Ref = resolution.ProtocolRef,
                    Type = resolution.ParsedRefId!.Type,
                    FactKind = resolution.FactKind,
                    Summary = resolution.Summary ?? "",
                },
            };
            return true;
        }
    }
}
