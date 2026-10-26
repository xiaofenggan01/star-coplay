using StardewValley.Menus;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors.Ui;

public static class MenuClassifier
{
    public static UiMenuFact Classify(IClickableMenu menu)
    {
        var fact = new UiMenuFact
        {
            MenuToken = RefIdTokenEncoder.EncodeProtocolKey(menu.GetType().Name),
            MenuType  = menu.GetType().Name,
            ActiveTab = GetActiveTab(menu),
            Title     = GetTitle(menu),
            Modal     = IsModal(menu),
        };

        // DialogueBox 专属字段
        if (menu is DialogueBox db)
        {
            fact.IsQuestion   = db.isQuestion;
            fact.DialogueText = GetDialogueText(db);
        }

        // ShopMenu 专属字段
        if (menu is ShopMenu shop)
            fact.ShopCurrency = shop.currency;

        return fact;
    }

    private static string GetActiveTab(IClickableMenu menu)
    {
        if (menu is GameMenu gm)
            return GameMenuTabNames.Get(gm.currentTab);
        return "";
    }

    private static string GetTitle(IClickableMenu menu) => menu switch
    {
        ShopMenu shop  => shop.ShopId ?? "",
        DialogueBox db => db.characterDialogue?.speaker?.Name ?? "",
        _              => "",
    };

    private static bool IsModal(IClickableMenu menu) =>
        menu is DialogueBox or LetterViewerMenu;

    /// <summary>
    /// 取 DialogueBox 当前显示的对话文本。
    /// 优先 getCurrentString()（处理 $ 指令后的干净文本），回退到 dialogues[0]。
    /// </summary>
    private static string GetDialogueText(DialogueBox db)
    {
        try
        {
            var text = db.getCurrentString();
            if (!string.IsNullOrEmpty(text)) return text;
        }
        catch { /* getCurrentString 可能抛异常，安全回退 */ }

        if (db.dialogues != null && db.dialogues.Count > 0)
            return db.dialogues[0] ?? "";

        return "";
    }
}
