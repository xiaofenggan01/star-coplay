using System.Collections.Generic;
using StardewValley;
using StardewValley.Menus;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.Query.Projectors.Ui.ElementExtractors;
using StarCoPlay.V2.RefId.Encoding;
using StarCoPlay.V2.RefId.Protocol;

namespace StarCoPlay.V2.RefId.Resolution;

public sealed class UiElementResolver : IRefIdResolver
{
    public RefIdResolutionResult Resolve(ParsedRefId parsedRefId)
    {
        var factKind = RefIdProtocolMapper.InferFactKind(parsedRefId);

        // Locator: [menu_token, index0]
        if (parsedRefId.LocatorParts.Count != 2)
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsedRefId, factKind,
                null, "UI element locator must have 2 parts: menu_token, index.");

        var menuToken = parsedRefId.LocatorParts[0];
        if (!int.TryParse(parsedRefId.LocatorParts[1], out var index))
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsedRefId, factKind,
                null, "Index must be a valid integer.");

        // No menu open → StaleContextMismatch
        var menu = Game1.activeClickableMenu;
        if (menu == null)
            return new RefIdResolutionResult(RefIdStatus.StaleContextMismatch, parsedRefId, factKind,
                null, "No menu is currently open.");

        // Menu token mismatch → StaleContextMismatch
        var currentToken = GetMenuToken(menu);
        if (currentToken != menuToken)
            return new RefIdResolutionResult(RefIdStatus.StaleContextMismatch, parsedRefId, factKind,
                null, $"Current menu token '{currentToken}' does not match ref token '{menuToken}'.");

        // Re-run extractor to get current element list
        var extractor = ResolveExtractor(menu);
        var elements = extractor.Extract(menu);

        // Index out of bounds → Missing
        if (index < 0 || index >= elements.Count)
            return new RefIdResolutionResult(RefIdStatus.Missing, parsedRefId, factKind,
                null, $"Index {index} out of bounds (elements count: {elements.Count}).");

        var element = elements[index];

        // Validate identity guard
        var elementGuard = element.IdentityGuard ?? "";
        if (elementGuard != parsedRefId.IdentityGuard)
            return new RefIdResolutionResult(RefIdStatus.StaleGuardMismatch, parsedRefId, factKind,
                null, $"Guard mismatch: expected '{elementGuard}', got '{parsedRefId.IdentityGuard}'.");

        // Success: Source.Value = UiElementFact
        return new RefIdResolutionResult(RefIdStatus.Resolved, parsedRefId, factKind, null, null,
            new ResolvedRefSource(element));
    }

    private static string GetMenuToken(IClickableMenu menu) => menu switch
    {
        GameMenu => "gamemenu",
        DialogueBox => "dialoguebox",
        ShopMenu => "shopmenu",
        _ => RefIdTokenEncoder.EncodeProtocolKey(menu.GetType().Name),
    };

    private static IMenuElementExtractor ResolveExtractor(IClickableMenu menu) => menu switch
    {
        DialogueBox => new DialogueBoxExtractor(),
        ShopMenu => new ShopMenuExtractor(),
        GameMenu => new GameMenuExtractor(),
        _ => new FallbackExtractor(),
    };
}
