using System.Collections.Generic;
using System.Globalization;
using StardewValley.Menus;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors.Ui.ElementExtractors;

public class FallbackExtractor : IMenuElementExtractor
{
    public List<UiElementFact> Extract(IClickableMenu menu)
    {
        var elements = new List<UiElementFact>();
        if (menu.allClickableComponents == null)
            return elements;

        var menuToken = RefIdTokenEncoder.EncodeProtocolKey(menu.GetType().Name);

        for (var i = 0; i < menu.allClickableComponents.Count; i++)
        {
            var comp = menu.allClickableComponents[i];
            var label = string.IsNullOrEmpty(comp.name) ? $"component_{i}" : comp.name;
            var guard = RefIdTokenEncoder.EncodeProtocolKey(label);

            var element = new UiElementFact
            {
                Ref = RefIdBuilder.Build(
                    "ui_element", "button",
                    new[] { menuToken, i.ToString(CultureInfo.InvariantCulture) },
                    guard),
                Type          = "button",
                ElementToken  = RefIdTokenEncoder.EncodeProtocolKey(label),
                IdentityGuard = guard,
                Label         = label,
                Visible       = comp.visible,
                Enabled       = true,
                Index         = i,
            };

            elements.Add(element);
        }

        return elements;
    }
}
