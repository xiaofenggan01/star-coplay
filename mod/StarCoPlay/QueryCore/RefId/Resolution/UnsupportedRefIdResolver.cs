using StarCoPlay.V2.RefId.Protocol;

namespace StarCoPlay.V2.RefId.Resolution;

public sealed class UnsupportedRefIdResolver : IRefIdResolver
{
    public RefIdResolutionResult Resolve(ParsedRefId parsedRefId)
    {
        return new RefIdResolutionResult(
            RefIdStatus.Unsupported,
            parsedRefId,
            RefIdProtocolMapper.InferFactKind(parsedRefId),
            $"Ref type '{parsedRefId.RefType}:{parsedRefId.Type}' is not supported yet.",
            "No runtime resolver has been registered for this ref type/type.");
    }
}
