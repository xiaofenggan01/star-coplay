using System.Collections.Generic;
using NUnit.Framework;
using StarCoPlay.GameServices.Naming;

namespace StarCoPlay.Tests.Naming;

/// <summary>
/// NameSimilarity 单元测试。
/// 所有测试纯内存运行，不依赖 SMAPI / Game1 / XNA。
/// </summary>
[TestFixture]
public class NameSimilarityTests
{
    // ═══════════════════════════════════════════════════════════════
    // LevenshteinDistance 经典样本
    // ═══════════════════════════════════════════════════════════════

    [Test]
    public void Levenshtein_KittenSitting_Returns3()
    {
        // 经典教材示例
        Assert.That(NameSimilarity.LevenshteinDistance("kitten", "sitting"), Is.EqualTo(3));
    }

    [Test]
    public void Levenshtein_IdenticalStrings_Returns0()
    {
        Assert.That(NameSimilarity.LevenshteinDistance("Abigail", "Abigail"), Is.EqualTo(0));
    }

    [Test]
    public void Levenshtein_CaseInsensitive_Returns0()
    {
        // 规范化为小写后应相等
        Assert.That(NameSimilarity.LevenshteinDistance("FARM", "farm"), Is.EqualTo(0));
    }

    [Test]
    public void Levenshtein_EmptyA_ReturnsLengthOfB()
    {
        Assert.That(NameSimilarity.LevenshteinDistance("", "abc"), Is.EqualTo(3));
    }

    [Test]
    public void Levenshtein_EmptyB_ReturnsLengthOfA()
    {
        Assert.That(NameSimilarity.LevenshteinDistance("abc", ""), Is.EqualTo(3));
    }

    [Test]
    public void Levenshtein_BothEmpty_Returns0()
    {
        Assert.That(NameSimilarity.LevenshteinDistance("", ""), Is.EqualTo(0));
    }

    [Test]
    public void Levenshtein_NullA_TreatedAsEmpty()
    {
        // null 视为空串
        Assert.That(NameSimilarity.LevenshteinDistance(null!, "abc"), Is.EqualTo(3));
    }

    [Test]
    public void Levenshtein_NullB_TreatedAsEmpty()
    {
        Assert.That(NameSimilarity.LevenshteinDistance("abc", null!), Is.EqualTo(3));
    }

    [Test]
    public void Levenshtein_SingleChar_Substitution_Returns1()
    {
        Assert.That(NameSimilarity.LevenshteinDistance("a", "b"), Is.EqualTo(1));
    }

    [Test]
    public void Levenshtein_SingleChar_Same_Returns0()
    {
        Assert.That(NameSimilarity.LevenshteinDistance("a", "a"), Is.EqualTo(0));
    }

    [Test]
    public void Levenshtein_Insertion_Returns1()
    {
        // "cat" → "cats"（插入 s）
        Assert.That(NameSimilarity.LevenshteinDistance("cat", "cats"), Is.EqualTo(1));
    }

    [Test]
    public void Levenshtein_Deletion_Returns1()
    {
        // "cats" → "cat"（删除 s）
        Assert.That(NameSimilarity.LevenshteinDistance("cats", "cat"), Is.EqualTo(1));
    }

    // ═══════════════════════════════════════════════════════════════
    // SuggestSimilar — CJK 路径（maxDistance=1 + 长度差 ≤ 1 过滤）
    // ═══════════════════════════════════════════════════════════════

    [Test]
    public void SuggestSimilar_CjkInput_ExactMatch_Included()
    {
        var candidates = new List<string> { "鸡舍", "大鸡舍", "豪华鸡舍", "畜棚" };
        var result = NameSimilarity.SuggestSimilar("鸡舍", candidates);
        // 完全相同（距离=0），应包含在结果里
        Assert.That(result, Does.Contain("鸡舍"));
    }

    [Test]
    public void SuggestSimilar_CjkInput_LengthFilterApplied()
    {
        // "鸡棚" vs "大鸡舍"（长度差=1，距离=2）→ 长度差 ≤ 1 但 CJK maxDist=1，应被过滤
        // "鸡棚" vs "鸡舍"（长度差=0，距离=1）→ 应命中
        var candidates = new List<string> { "鸡舍", "大鸡舍", "豪华鸡舍" };
        var result = NameSimilarity.SuggestSimilar("鸡棚", candidates);

        Assert.That(result, Does.Contain("鸡舍"), "鸡棚→鸡舍 距离=1，应命中");
        Assert.That(result, Does.Not.Contain("豪华鸡舍"), "豪华鸡舍长度差=2，应被过滤");
    }

    [Test]
    public void SuggestSimilar_CjkInput_LongCandidateFiltered()
    {
        // "鸡舍" 长度=2，"豪华鸡舍" 长度=4（差=2），应被 CJK 长度差过滤
        var candidates = new List<string> { "豪华鸡舍", "超级豪华鸡舍" };
        var result = NameSimilarity.SuggestSimilar("鸡舍", candidates);
        Assert.That(result, Is.Empty, "长度差 > 1 的 CJK 候选应全部被过滤");
    }

    [Test]
    public void SuggestSimilar_CjkInput_MaxResults_Respected()
    {
        // 3 个距离=1 的候选，maxResults=2 时只返回前 2
        var candidates = new List<string> { "鸡舍", "鸡舍a", "鸡舍b" };
        // 只有"鸡舍"是纯 CJK，其余含字母，以 CJK 输入为准
        var result = NameSimilarity.SuggestSimilar("鸡舍", candidates, maxResults: 2);
        Assert.That(result.Count, Is.LessThanOrEqualTo(2));
    }

    // ═══════════════════════════════════════════════════════════════
    // SuggestSimilar — 英文路径（maxDistance=2 默认）
    // ═══════════════════════════════════════════════════════════════

    [Test]
    public void SuggestSimilar_English_Distance1_Included()
    {
        var candidates = new List<string> { "Abigail", "Alex", "Emily" };
        var result = NameSimilarity.SuggestSimilar("Abigai", candidates); // 少一个 l
        Assert.That(result, Does.Contain("Abigail"));
    }

    [Test]
    public void SuggestSimilar_English_Distance2_Included()
    {
        var candidates = new List<string> { "Abigail", "Alex" };
        var result = NameSimilarity.SuggestSimilar("Abigal", candidates); // 差 2
        Assert.That(result, Does.Contain("Abigail"));
    }

    [Test]
    public void SuggestSimilar_English_Distance3_Excluded()
    {
        var candidates = new List<string> { "Abigail" };
        var result = NameSimilarity.SuggestSimilar("Abig", candidates); // 差 3，超出默认 maxDist=2
        Assert.That(result, Is.Empty);
    }

    [Test]
    public void SuggestSimilar_English_SortedByDistance()
    {
        var candidates = new List<string> { "FarmHouse", "Farm", "Forest" };
        // "Farm" 距离=0，"FarmHouse" 距离=5（超出 2）
        var result = NameSimilarity.SuggestSimilar("Farm", candidates);
        // "Farm" 应排在首位（距离最小）
        Assert.That(result[0], Is.EqualTo("Farm"));
    }

    [Test]
    public void SuggestSimilar_English_MaxResultsDefault3()
    {
        // 5 个距离 ≤ 2 的候选，默认返回 3
        var candidates = new List<string> { "aa", "ab", "ac", "ad", "ae" };
        var result = NameSimilarity.SuggestSimilar("aa", candidates, maxResults: 3);
        Assert.That(result.Count, Is.EqualTo(3));
    }

    // ═══════════════════════════════════════════════════════════════
    // SuggestSimilar — 边界 & 异常输入
    // ═══════════════════════════════════════════════════════════════

    [Test]
    public void SuggestSimilar_NullInput_ReturnsEmpty()
    {
        var candidates = new List<string> { "Farm", "Town" };
        var result = NameSimilarity.SuggestSimilar(null!, candidates);
        Assert.That(result, Is.Empty);
    }

    [Test]
    public void SuggestSimilar_EmptyInput_ReturnsEmpty()
    {
        var candidates = new List<string> { "Farm", "Town" };
        var result = NameSimilarity.SuggestSimilar("", candidates);
        Assert.That(result, Is.Empty);
    }

    [Test]
    public void SuggestSimilar_NullCandidates_ReturnsEmpty()
    {
        var result = NameSimilarity.SuggestSimilar("Farm", null!);
        Assert.That(result, Is.Empty);
    }

    [Test]
    public void SuggestSimilar_EmptyCandidates_ReturnsEmpty()
    {
        var result = NameSimilarity.SuggestSimilar("Farm", new List<string>());
        Assert.That(result, Is.Empty);
    }

    [Test]
    public void SuggestSimilar_NullAndEmptyCandidatesFiltered()
    {
        // 候选中混有 null / empty，应被忽略不报错
        var candidates = new List<string?> { null, "", "Farm" }!;
        var result = NameSimilarity.SuggestSimilar("Farm", candidates!);
        Assert.That(result, Does.Contain("Farm"));
        Assert.That(result.Count, Is.EqualTo(1));
    }

    [Test]
    public void SuggestSimilar_AllCandidatesTooFar_ReturnsEmpty()
    {
        var candidates = new List<string> { "completely_unrelated_string_xyz" };
        var result = NameSimilarity.SuggestSimilar("abc", candidates);
        Assert.That(result, Is.Empty);
    }
}
