using System.Collections.Generic;
using System.Globalization;
using StardewValley;
using StardewValley.Menus;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors.Ui.ElementExtractors;

public class ShopMenuExtractor : IMenuElementExtractor
{
    private const string MenuToken = "shopmenu";

    public List<UiElementFact> Extract(IClickableMenu menu)
    {
        var elements = new List<UiElementFact>();
        if (menu is not ShopMenu shop || shop.forSale == null)
            return elements;

        for (var i = 0; i < shop.forSale.Count; i++)
        {
            var salable = shop.forSale[i];
            if (salable == null) continue;
            var displayName = salable.DisplayName ?? salable.Name ?? "unknown";
            var qualifiedItemId = salable.QualifiedItemId ?? "";
            var guard = BuildGuard(salable, qualifiedItemId);

            var price = 0;
            var stock = 0;
            var tradeItemId = "";

            if (shop.itemPriceAndStock != null &&
                shop.itemPriceAndStock.TryGetValue(salable, out var stockInfo))
            {
                price = stockInfo.Price;
                stock = stockInfo.Stock == int.MaxValue ? -1 : stockInfo.Stock;
                tradeItemId = stockInfo.TradeItem ?? "";
            }

            var element = new UiElementFact
            {
                Ref = RefIdBuilder.Build(
                    "ui_element", "shop_item",
                    new[] { MenuToken, i.ToString(CultureInfo.InvariantCulture) },
                    guard),
                Type            = "shop_item",
                ElementToken    = RefIdTokenEncoder.EncodeProtocolKey(displayName),
                IdentityGuard   = guard,
                Label           = displayName,
                QualifiedItemId = qualifiedItemId,
                Price           = price,
                Stock           = stock,
                TradeItemId     = tradeItemId,
                Visible         = true,
                Enabled         = true,
                Index           = i,
            };

            elements.Add(element);
        }

        return elements;
    }

    private static string BuildGuard(ISalable salable, string qualifiedItemId)
    {
        if (!string.IsNullOrEmpty(qualifiedItemId))
            return RefIdTokenEncoder.EncodeQualifiedItemId(qualifiedItemId);

        return RefIdTokenEncoder.EncodeProtocolKey(salable.Name ?? "unknown");
    }
}
