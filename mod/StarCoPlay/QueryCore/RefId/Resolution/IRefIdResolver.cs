namespace StarCoPlay.V2.RefId.Resolution;

public interface IRefIdResolver
{
    RefIdResolutionResult Resolve(ParsedRefId parsedRefId);
}
