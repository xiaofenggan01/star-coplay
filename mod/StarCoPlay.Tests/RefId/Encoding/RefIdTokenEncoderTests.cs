using NUnit.Framework;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.Tests.RefId.Encoding;

[TestFixture]
public class RefIdTokenEncoderTests
{
    [TestCase("(O)388", "o_388")]
    [TestCase("(BC)130", "bc_130")]
    [TestCase("(F)Bed", "f_bed")]
    [TestCase("(T)Axe", "t_axe")]
    public void EncodeQualifiedItemId_UsesTypedSafeToken(string raw, string expected)
    {
        Assert.That(RefIdTokenEncoder.EncodeQualifiedItemId(raw), Is.EqualTo(expected));
    }

    [TestCase("Farm", "farm")]
    [TestCase("FarmHouse", "farmhouse")]
    [TestCase("Coop82b38ef3-ac7b-4256-bc15-14aa9b59abe2", "coop82b38ef3-ac7b-4256-bc15-14aa9b59abe2")]
    public void EncodeMapToken_NormalizesLocationNames(string raw, string expected)
    {
        Assert.That(RefIdTokenEncoder.EncodeMapToken(raw), Is.EqualTo(expected));
    }

    [TestCase("Black Cat", "black_cat")]
    [TestCase("Mr Qi", "mr_qi")]
    [TestCase("dialogue sleep", "dialogue_sleep")]
    public void EncodeProtocolKey_NormalizesSpaces(string raw, string expected)
    {
        Assert.That(RefIdTokenEncoder.EncodeProtocolKey(raw), Is.EqualTo(expected));
    }

    [TestCase("a:b", "a_x3ab")]
    [TestCase("a#b", "a_x23b")]
    [TestCase("Data/Objects", "data_x2fobjects")]
    [TestCase("(O)388", "_x28o_x29388")]
    public void EncodeProtocolKey_DoesNotLeaveUnsafeCharactersRaw(string raw, string expected)
    {
        Assert.That(RefIdTokenEncoder.EncodeProtocolKey(raw), Is.EqualTo(expected));
    }

    [TestCase("1", "wt_1")]
    [TestCase("Oak", "wt_oak")]
    public void EncodeWildTreeType_AddsTreePrefix(string raw, string expected)
    {
        Assert.That(RefIdTokenEncoder.EncodeWildTreeType(raw), Is.EqualTo(expected));
    }

    [TestCase("stone", "rn_stone")]
    [TestCase("ore copper", "rn_ore_copper")]
    public void EncodeResourceNodeKind_AddsResourceNodePrefix(string raw, string expected)
    {
        Assert.That(RefIdTokenEncoder.EncodeResourceNodeKind(raw), Is.EqualTo(expected));
    }

    [TestCase("farm")]
    [TestCase("farmhouse")]
    [TestCase("coop82b38ef3-ac7b-4256-bc15-14aa9b59abe2")]
    [TestCase("o_388")]
    [TestCase("bc_130")]
    [TestCase("wt_1")]
    public void IsSafeToken_AcceptsProtocolSafeTokens(string value)
    {
        Assert.That(RefIdTokenEncoder.IsSafeToken(value), Is.True);
    }

    [TestCase("")]
    [TestCase(" ")]
    [TestCase("black cat")]
    [TestCase("a:b")]
    [TestCase("a#b")]
    [TestCase("Data/Objects")]
    [TestCase("(O)388")]
    [TestCase("木头")]
    [TestCase("a.b")]
    public void IsSafeToken_RejectsUnsafeTokens(string value)
    {
        Assert.That(RefIdTokenEncoder.IsSafeToken(value), Is.False);
    }
}
