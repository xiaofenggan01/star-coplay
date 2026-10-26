using System;
using System.Collections.Generic;
using System.Linq;

namespace StarCoPlay.GameServices.Naming;

/// <summary>
/// 字符串相似度工具，用于目标名称的模糊匹配与候选建议。
/// </summary>
public static class NameSimilarity
{
    /// <summary>
    /// 计算两个字符串之间的 Levenshtein 编辑距离（大小写不敏感）。
    /// </summary>
    /// <param name="a">字符串 A</param>
    /// <param name="b">字符串 B</param>
    /// <returns>编辑距离（0 = 完全相同）</returns>
    public static int LevenshteinDistance(string a, string b)
    {
        if (string.IsNullOrEmpty(a)) return b?.Length ?? 0;
        if (string.IsNullOrEmpty(b)) return a.Length;

        // 规范化为小写
        a = a.ToLowerInvariant();
        b = b.ToLowerInvariant();

        int m = a.Length;
        int n = b.Length;

        // 使用滚动数组节省内存
        var prev = new int[n + 1];
        var curr = new int[n + 1];

        for (int j = 0; j <= n; j++)
            prev[j] = j;

        for (int i = 1; i <= m; i++)
        {
            curr[0] = i;
            for (int j = 1; j <= n; j++)
            {
                int cost = a[i - 1] == b[j - 1] ? 0 : 1;
                curr[j] = Math.Min(
                    Math.Min(curr[j - 1] + 1,   // 插入
                             prev[j] + 1),        // 删除
                    prev[j - 1] + cost);           // 替换
            }
            Array.Copy(curr, prev, n + 1);
        }

        return prev[n];
    }

    /// <summary>
    /// 从候选集合中找出与输入最相似的若干项（编辑距离 ≤ maxDistance）。
    /// 对 CJK 字符自动收紧匹配：maxDistance=1 且长度差 ≤ 1，避免"鸡棚"同时匹中"鸡舍/大鸡舍/豪华鸡舍"。
    /// </summary>
    /// <param name="input">用户输入的目标名</param>
    /// <param name="candidates">候选字符串集合</param>
    /// <param name="maxDistance">最大允许编辑距离（默认 2；CJK 输入时自动降为 1）</param>
    /// <param name="maxResults">最多返回几条（默认 3）</param>
    /// <returns>按编辑距离升序排列的相似候选列表</returns>
    public static List<string> SuggestSimilar(
        string input,
        IEnumerable<string> candidates,
        int maxDistance = 2,
        int maxResults = 3)
    {
        if (string.IsNullOrEmpty(input) || candidates == null)
            return new List<string>();

        // P1-4 修复：CJK 字符语义偏差 — 收紧 maxDistance + 加长度差过滤
        bool isCjk = ContainsCjk(input);
        int actualMaxDistance = isCjk ? 1 : maxDistance;

        return candidates
            .Where(c => !string.IsNullOrEmpty(c))
            .Where(c => !isCjk || Math.Abs(c.Length - input.Length) <= 1)
            .Select(c => (candidate: c, dist: LevenshteinDistance(input, c)))
            .Where(x => x.dist <= actualMaxDistance)
            .OrderBy(x => x.dist)
            .Take(maxResults)
            .Select(x => x.candidate)
            .ToList();
    }

    /// <summary>
    /// 检测字符串是否包含 CJK 统一汉字（U+4E00–U+9FFF）。
    /// </summary>
    private static bool ContainsCjk(string s)
    {
        foreach (char c in s)
        {
            if (c >= 0x4E00 && c <= 0x9FFF) return true;
        }
        return false;
    }
}
