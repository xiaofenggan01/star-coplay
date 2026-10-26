using System.Collections.Generic;

namespace StarCoPlay.V2.RefId.Resolution;

public sealed class RefIdResolverDispatcher : IRefIdResolver
{
    private readonly Dictionary<string, IRefIdResolver> _resolversByRefType = new();
    private readonly UnsupportedRefIdResolver _fallback = new();

    public void Register(string refType, IRefIdResolver resolver)
    {
        _resolversByRefType[refType] = resolver;
    }

    public RefIdResolutionResult Resolve(ParsedRefId parsedRefId)
    {
        if (_resolversByRefType.TryGetValue(parsedRefId.RefType, out var resolver))
            return resolver.Resolve(parsedRefId);

        return _fallback.Resolve(parsedRefId);
    }
}
