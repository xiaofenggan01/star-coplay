export interface AdminUserSearchRow {
  user_id?: string | null;
  email?: string | null;
  nickname?: string | null;
}

export function normalizeAdminUserSearchQuery(value: string | null | undefined): string {
  return value?.trim().toLowerCase() ?? "";
}

export function adminUserMatchesSearch(row: AdminUserSearchRow, query: string): boolean {
  const normalized = normalizeAdminUserSearchQuery(query);
  if (!normalized) return true;

  return [row.email, row.user_id, row.nickname].some((value) =>
    value?.toLowerCase().includes(normalized),
  );
}

export function filterAdminUsersBySearch<T extends AdminUserSearchRow>(rows: T[], query: string | null | undefined): T[] {
  const normalized = normalizeAdminUserSearchQuery(query);
  if (!normalized) return rows;
  return rows.filter((row) => adminUserMatchesSearch(row, normalized));
}
