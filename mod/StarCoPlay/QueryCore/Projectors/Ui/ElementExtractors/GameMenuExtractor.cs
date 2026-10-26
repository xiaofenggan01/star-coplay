using System.Collections.Generic;
using System.Globalization;
using StardewValley.Menus;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors.Ui.ElementExtractors;

public class GameMenuExtractor : IMenuElementExtractor
{
    private const string MenuToken = "gamemenu";

    public List<UiElementFact> Extract(IClickableMenu menu)
    {
        var elements = new List<UiElementFact>();
        if (menu is not GameMenu gm || gm.tabs == null)
            return elements;

        for (var i = 0; i < gm.tabs.Count && i < GameMenuTabNames.Names.Length; i++)
        {
            var tabName = GameMenuTabNames.Names[i];
            var guard = RefIdTokenEncoder.EncodeProtocolKey(tabName);

            var element = new UiElementFact
            {
                Ref = RefIdBuilder.Build(
                    "ui_element", "tab",
                    new[] { MenuToken, i.ToString(CultureInfo.InvariantCulture) },
                    guard),
                Type          = "tab",
                ElementToken  = tabName,
                IdentityGuard = guard,
                Label         = tabName,
                Visible       = true,
                Enabled       = i != gm.currentTab,
                Index         = i,
            };

            elements.Add(element);
        }

        return elements;
    }
}
