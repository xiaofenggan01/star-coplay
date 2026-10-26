const INVITE_QUERY_KEYS = ["ref", "invite_code", "inviteCode", "code"] as const;

export function readInviteCodeFromSearch(search: string | URLSearchParams): string {
  const params =
    typeof search === "string"
      ? new URLSearchParams(search.startsWith("?") ? search.slice(1) : search)
      : search;

  for (const key of INVITE_QUERY_KEYS) {
    const value = params.get(key)?.trim().normalize("NFKC");
    if (value) return value;
  }

  return "";
}
