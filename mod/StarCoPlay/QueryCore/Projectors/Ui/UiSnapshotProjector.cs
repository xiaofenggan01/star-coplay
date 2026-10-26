using StardewValley;
using StardewValley.Menus;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.Query.Projectors.Ui.ElementExtractors;

namespace StarCoPlay.V2.Query.Projectors.Ui;

public static class UiSnapshotProjector
{
    public static UiSnapshotFact Project(bool includeElements)
    {
        var menu = Game1.activeClickableMenu;

        if (menu == null)
            return new UiSnapshotFact { MenuOpen = false };

        var fact = new UiSnapshotFact
        {
            MenuOpen = true,
            Menu = MenuClassifier.Classify(menu),
        };

        if (includeElements)
        {
            var extractor = ResolveExtractor(menu);
            fact.Elements.AddRange(extractor.Extract(menu));
        }

        return fact;
    }

    private static IMenuElementExtractor ResolveExtractor(IClickableMenu menu) => menu switch
    {
        DialogueBox    => new DialogueBoxExtractor(),
        ShopMenu       => new ShopMenuExtractor(),
        GameMenu       => new GameMenuExtractor(),
        _              => new FallbackExtractor(),
    };
}
