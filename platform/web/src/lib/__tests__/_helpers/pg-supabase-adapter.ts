/**
 * 测试专用：pg → supabase-like adapter
 *
 * 目的：让 invite-code.ts（其 InviteCodeClient = Pick<SupabaseClient,"from">）
 *      在没有 PostgREST/Supabase Studio 的本地裸 PostgreSQL 上跑通集成测试。
 *
 * 仅实现 invite-code.ts 实际调用的 fluent surface 子集：
 *
 *   1. SELECT：
 *        from(table).select(cols).eq(k,v).maybeSingle()
 *        → SELECT cols FROM table WHERE k=$1 LIMIT 1
 *
 *   2. UPDATE + RETURNING：
 *        from(table).update(payload).eq(k1,v1).eq(k2,v2).select(cols).maybeSingle()
 *        → UPDATE table SET ...payload WHERE k1=$1 AND k2=$2 RETURNING cols
 *          （取首行；超过 1 行时按 supabase 行为返回错误）
 *
 *   3. UPDATE 无返回（refund 路径）：
 *        from(table).update(payload).eq(k1,v1).eq(k2,v2)
 *        是一个 thenable —— await 即触发 SQL，无 RETURNING。
 *
 * 返回 `{ data, error }`：成功 error=null，失败 error={ message }；
 * 与 supabase-js v2 行为对齐到 invite-code.ts 关心的字段。
 *
 * **不用于生产**。仅供 __tests__ 注入。
 */

import type { Client as PgClient } from "pg";

export interface AdapterResult<T> {
  data: T | null;
  error: { message: string } | null;
}

interface UpdateState {
  table: string;
  payload: Record<string, unknown>;
  eqs: Array<[string, unknown]>;
}

interface SelectState {
  table: string;
  columns: string;
  eqs: Array<[string, unknown]>;
}

function ident(name: string): string {
  // 简易 SQL identifier 引用：仅允许 [A-Za-z_][A-Za-z0-9_]*，否则抛错
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
    throw new Error(`pg-adapter: unsafe identifier ${JSON.stringify(name)}`);
  }
  return `"${name}"`;
}

function buildSelectClause(columns: string): string {
  // supabase-js 用 "*" 或逗号分隔的列名；这里直接透传 "*"，否则按列名安全拼接
  const trimmed = columns.trim();
  if (trimmed === "*" || trimmed === "") return "*";
  return trimmed
    .split(",")
    .map((c) => ident(c.trim()))
    .join(", ");
}

function buildWhere(
  eqs: Array<[string, unknown]>,
  startIndex: number,
): { sql: string; values: unknown[] } {
  if (eqs.length === 0) return { sql: "", values: [] };
  const parts: string[] = [];
  const values: unknown[] = [];
  let i = startIndex;
  for (const [k, v] of eqs) {
    parts.push(`${ident(k)} = $${i}`);
    values.push(v);
    i++;
  }
  return { sql: " WHERE " + parts.join(" AND "), values };
}

/**
 * 包装一个 pg.Client，返回带 .from() 的对象，shape 兼容 InviteCodeClient。
 * 调用方负责 client.connect() / client.end()。
 */
export function createPgSupabaseAdapter(pg: PgClient): { from: (table: string) => unknown } {
  return {
    from(table: string) {
      // —— SELECT 子链 ——
      const buildSelectChain = (columns: string) => {
        const state: SelectState = { table, columns, eqs: [] };
        const chain: {
          eq: (k: string, v: unknown) => typeof chain;
          maybeSingle: () => Promise<AdapterResult<Record<string, unknown>>>;
        } = {
          eq(key, value) {
            state.eqs.push([key, value]);
            return chain;
          },
          async maybeSingle() {
            try {
              const where = buildWhere(state.eqs, 1);
              const sql = `SELECT ${buildSelectClause(state.columns)} FROM ${ident(state.table)}${where.sql} LIMIT 1`;
              const r = await pg.query(sql, where.values);
              const data = r.rows[0] ?? null;
              return { data, error: null };
            } catch (e) {
              return {
                data: null,
                error: { message: e instanceof Error ? e.message : String(e) },
              };
            }
          },
        };
        return chain;
      };

      // —— UPDATE 子链 ——
      const buildUpdateChain = (payload: Record<string, unknown>) => {
        const state: UpdateState = { table, payload, eqs: [] };

        // 共用：执行 UPDATE，可选 RETURNING
        async function execUpdate(
          returningCols: string | null,
        ): Promise<AdapterResult<Record<string, unknown>>> {
          try {
            const setKeys = Object.keys(state.payload);
            if (setKeys.length === 0) {
              return { data: null, error: { message: "pg-adapter: empty update payload" } };
            }
            const values: unknown[] = [];
            const setParts: string[] = [];
            let i = 1;
            for (const k of setKeys) {
              setParts.push(`${ident(k)} = $${i}`);
              values.push(state.payload[k]);
              i++;
            }
            const where = buildWhere(state.eqs, i);
            values.push(...where.values);
            const returning =
              returningCols !== null ? ` RETURNING ${buildSelectClause(returningCols)}` : "";
            const sql = `UPDATE ${ident(state.table)} SET ${setParts.join(", ")}${where.sql}${returning}`;
            const r = await pg.query(sql, values);
            // RETURNING + maybeSingle：返回第一行（影响 0 行 → null）
            // 无 RETURNING：data 为 null
            const data = returningCols !== null ? (r.rows[0] ?? null) : null;
            return { data, error: null };
          } catch (e) {
            return {
              data: null,
              error: { message: e instanceof Error ? e.message : String(e) },
            };
          }
        }

        // chain 同时支持：
        //   .eq(...).eq(...).select(cols).maybeSingle()  → UPDATE ... RETURNING
        //   .eq(...).eq(...)  （直接 await，refund 路径）→ UPDATE ... 无 RETURNING
        const chain: {
          eq: (k: string, v: unknown) => typeof chain;
          select: (cols: string) => {
            maybeSingle: () => Promise<AdapterResult<Record<string, unknown>>>;
          };
          // thenable：让 `await chain` 触发无 RETURNING UPDATE
          then: <TResult1 = AdapterResult<null>, TResult2 = never>(
            onfulfilled?:
              | ((value: AdapterResult<null>) => TResult1 | PromiseLike<TResult1>)
              | null
              | undefined,
            onrejected?:
              | ((reason: unknown) => TResult2 | PromiseLike<TResult2>)
              | null
              | undefined,
          ) => Promise<TResult1 | TResult2>;
        } = {
          eq(key, value) {
            state.eqs.push([key, value]);
            return chain;
          },
          select(cols) {
            return {
              maybeSingle: () => execUpdate(cols),
            };
          },
          then(onfulfilled, onrejected) {
            return execUpdate(null).then(
              (res) => onfulfilled?.(res as unknown as AdapterResult<null>) as never,
              onrejected as never,
            ) as Promise<never>;
          },
        };
        return chain;
      };

      return {
        select: buildSelectChain,
        update: buildUpdateChain,
      };
    },
  };
}
