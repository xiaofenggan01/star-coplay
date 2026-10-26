using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Resolution;

namespace StarCoPlay.V2.RefId.Inspection;

public sealed class NullRefFactProjector : IRefFactProjector
{
    public bool TryProject(RefIdResolutionResult resolution, out InspectedRef inspectedRef)
    {
        inspectedRef = new InspectedRef();
        return false;
    }
}
