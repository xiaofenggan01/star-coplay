using System.Collections.Generic;
using System.Globalization;
using StardewValley.Menus;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors.Ui.ElementExtractors;

public class DialogueBoxExtractor : IMenuElementExtractor
{
    private const string MenuToken = "dialoguebox";

    public List<UiElementFact> Extract(IClickableMenu menu)
    {
        var elements = new List<UiElementFact>();
        if (menu is not DialogueBox db || !db.isQuestion || db.responses == null)
            return elements;

        for (var i = 0; i < db.responses.Length; i++)
        {
            var response = db.responses[i];
            if (response == null) continue;
            var responseKey = response.responseKey ?? "";
            var responseText = response.responseText ?? "";
            var guard = RefIdTokenEncoder.EncodeProtocolKey(responseKey);

            var element = new UiElementFact
            {
                Ref = RefIdBuilder.Build(
                    "ui_element", "dialogue_option",
                    new[] { MenuToken, i.ToString(CultureInfo.InvariantCulture) },
                    guard),
                Type          = "dialogue_option",
                ElementToken  = RefIdTokenEncoder.EncodeProtocolKey(Truncate(responseText, 30)),
                IdentityGuard = guard,
                Label         = responseText,
                ResponseKey   = responseKey,
                Visible       = true,
                Enabled       = true,
                Index         = i,
            };

            elements.Add(element);
        }

        return elements;
    }

    private static string Truncate(string text, int maxLen) =>
        text.Length <= maxLen ? text : text[..maxLen];
}
