using System.Collections.Generic;
using StardewValley.Menus;
using StarCoPlay.Protocol.V245;

namespace StarCoPlay.V2.Query.Projectors.Ui.ElementExtractors;

public interface IMenuElementExtractor
{
    List<UiElementFact> Extract(IClickableMenu menu);
}
