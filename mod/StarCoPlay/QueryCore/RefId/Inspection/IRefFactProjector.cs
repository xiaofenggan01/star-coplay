using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Resolution;

namespace StarCoPlay.V2.RefId.Inspection;

public interface IRefFactProjector
{
    bool TryProject(RefIdResolutionResult resolution, out InspectedRef inspectedRef);
}
