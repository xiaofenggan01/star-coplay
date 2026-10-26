namespace StarCoPlay.V2.Query.Projectors.Ui;

/// <summary>
/// SDV 1.6 GameMenu tab 索引到语义名的映射（共享常量，避免 MenuClassifier / GameMenuExtractor 漂移）。
/// 来源：GameMenu.cs 中的静态常量字段（inventoryTab=0 ... exitTab=9）。
/// </summary>
internal static class GameMenuTabNames
{
    /// <summary>按 GameMenu.currentTab 索引访问，长度 = 10。</summary>
    internal static readonly string[] Names =
    {
        "inventory",   // 0
        "skills",      // 1
        "social",      // 2
        "map",         // 3
        "crafting",    // 4
        "animals",     // 5  SDV 1.6 新增
        "powers",      // 6  SDV 1.6 新增
        "collections", // 7
        "options",     // 8
        "exit",        // 9
    };

    internal static string Get(int tabIndex) =>
        tabIndex >= 0 && tabIndex < Names.Length ? Names[tabIndex] : "";
}
