using StardewModdingAPI;
using System;

namespace StarCoPlay.Integrations
{
    /// <summary>
    /// Generic Mod Config Menu (GMCM) API 接口。
    /// 签名必须与 spacechase0.GenericModConfigMenu 1.16.x 精确匹配（Pintail 代理要求）。
    /// 参考：https://github.com/spacechase0/StardewValleyMods/tree/develop/GenericModConfigMenu
    /// </summary>
    public interface IGenericModConfigMenuApi
    {
        void Register(IManifest mod, Action reset, Action save, bool titleScreenOnly = false);

        void AddSectionTitle(IManifest mod, Func<string> text, Func<string>? tooltip = null);

        void AddParagraph(IManifest mod, Func<string> text);

        void AddBoolOption(IManifest mod, Func<bool> getValue, Action<bool> setValue,
            Func<string> name, Func<string>? tooltip = null, string? fieldId = null);

        void AddTextOption(IManifest mod, Func<string> getValue, Action<string> setValue,
            Func<string> name, Func<string>? tooltip = null,
            string[]? allowedValues = null, Func<string, string>? formatAllowedValue = null,
            string? fieldId = null);

        void AddNumberOption(IManifest mod, Func<int> getValue, Action<int> setValue,
            Func<string> name, Func<string>? tooltip = null,
            int? min = null, int? max = null, int? interval = null,
            Func<int, string>? formatValue = null, string? fieldId = null);

        void Unregister(IManifest mod);
    }
}
