using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Parsing;
using StarCoPlay.V2.RefId.Protocol;
using StarCoPlay.V2.RefId.Resolution;

namespace StarCoPlay.V2.RefId.Inspection;

public sealed class RefIdInspector
{
    private readonly IRefIdResolver resolver;
    private readonly IRefFactProjector projector;

    public RefIdInspector(IRefIdResolver resolver)
        : this(resolver, new NullRefFactProjector())
    {
    }

    public RefIdInspector(IRefIdResolver resolver, IRefFactProjector projector)
    {
        this.resolver = resolver;
        this.projector = projector;
    }

    public InspectedRef Inspect(string refId, bool includeDebug = false)
    {
        var parseResult = RefIdParser.Parse(refId);
        if (!parseResult.IsValid)
        {
            return FromResolution(RefIdProtocolMapper.CreateMalformedResolution(refId, parseResult.DebugReason ?? "Malformed ref."));
        }

        var resolution = resolver.Resolve(parseResult.ParsedRefId!);
        var protocolResolution = resolution.ToProtocolResolution(refId);
        var inspected = FromResolution(protocolResolution);

        if (resolution.Status is RefIdStatus.Resolved or RefIdStatus.ResolvedDegraded)
        {
            bool projected = false;

            if (projector.TryProject(resolution, out var projectedRef))
            {
                CopyEnvelope(projectedRef, inspected);
                inspected = projectedRef;
                projected = true;
            }

            if (includeDebug)
            {
                inspected.Debug.Add(new StringPair
                {
                    Key = "resolver_status",
                    Value = resolution.Status.ToString(),
                });
                inspected.Debug.Add(new StringPair
                {
                    Key = "source_type",
                    Value = resolution.Source?.Value?.GetType().Name ?? "null",
                });
                inspected.Debug.Add(new StringPair
                {
                    Key = "projector",
                    Value = projected ? projector.GetType().Name : "none",
                });
            }
        }

        return inspected;
    }

    private static InspectedRef FromResolution(RefResolution resolution)
    {
        return new InspectedRef
        {
            Resolution = resolution,
            Ref = resolution.Ref?.Value ?? "",
            RefType = resolution.RefType,
            Type = resolution.Type,
            FactKind = resolution.FactKind,
            Summary = resolution.Summary,
            DebugReason = resolution.DebugReason,
        };
    }

    private static void CopyEnvelope(InspectedRef target, InspectedRef source)
    {
        target.Resolution = source.Resolution;
        target.Ref = source.Ref;
        target.RefType = source.RefType;
        target.Type = source.Type;
        target.FactKind = source.FactKind;
        target.Summary = source.Summary;
        target.DebugReason = source.DebugReason;
    }
}
