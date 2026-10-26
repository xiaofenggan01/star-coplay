using System;
using System.Globalization;
using System.Text;

namespace StarCoPlay.V2.RefId.Encoding;

public static class RefIdTokenEncoder
{
    public static bool IsSafeToken(string? value)
    {
        if (string.IsNullOrEmpty(value))
            return false;

        foreach (var ch in value)
        {
            if (IsSafeTokenChar(ch))
                continue;

            return false;
        }

        return true;
    }

    public static string EncodeProtocolKey(string raw)
    {
        if (raw == null)
            throw new ArgumentNullException(nameof(raw));

        var trimmed = raw.Trim().ToLowerInvariant();
        if (trimmed.Length == 0)
            throw new ArgumentException("Token source cannot be empty.", nameof(raw));

        var builder = new StringBuilder(trimmed.Length);
        foreach (var ch in trimmed)
        {
            if (IsSafeTokenChar(ch))
            {
                builder.Append(ch);
            }
            else if (char.IsWhiteSpace(ch))
            {
                builder.Append('_');
            }
            else
            {
                builder.Append("_x");
                builder.Append(((int)ch).ToString("x", CultureInfo.InvariantCulture));
            }
        }

        return builder.ToString();
    }

    public static string EncodeMapToken(string nameOrUniqueName)
    {
        return EncodeProtocolKey(nameOrUniqueName);
    }

    public static string EncodeQualifiedItemId(string qualifiedItemId)
    {
        if (qualifiedItemId == null)
            throw new ArgumentNullException(nameof(qualifiedItemId));

        var trimmed = qualifiedItemId.Trim();
        if (trimmed.Length == 0)
            throw new ArgumentException("Qualified item id cannot be empty.", nameof(qualifiedItemId));

        if (trimmed[0] == '(')
        {
            var closeIndex = trimmed.IndexOf(')');
            if (closeIndex > 1 && closeIndex < trimmed.Length - 1)
            {
                var typeId = trimmed[1..closeIndex];
                var itemId = trimmed[(closeIndex + 1)..];
                return $"{EncodeProtocolKey(typeId)}_{EncodeProtocolKey(itemId)}";
            }
        }

        return EncodeProtocolKey(trimmed);
    }

    public static string EncodeWildTreeType(string treeType)
    {
        return AddPrefix("wt", treeType);
    }

    public static string EncodeResourceNodeKind(string nodeKind)
    {
        return AddPrefix("rn", nodeKind);
    }

    private static string AddPrefix(string prefix, string raw)
    {
        return $"{prefix}_{EncodeProtocolKey(raw)}";
    }

    private static bool IsSafeTokenChar(char ch)
    {
        return ch is >= 'a' and <= 'z'
            || ch is >= 'A' and <= 'Z'
            || ch is >= '0' and <= '9'
            || ch == '_'
            || ch == '-';
    }
}
