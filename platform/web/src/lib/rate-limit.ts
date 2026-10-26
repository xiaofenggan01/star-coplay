/**
 * 内存版滑动窗口限流（per-key, per-minute）。
 *
 * 设计：
 *   - 用 Map<key, timestamps[]> 维护每个 key 在最近 60s 内的请求时间戳数组
 *   - 调用时先移除窗口外（>= 60s 前）的记录；若剩余数 < maxPerMinute 则放行并 push 当前时刻
 *   - 简单、无依赖、无副作用（除 Map 本身）；测试可用 fake timers 控时
 *
 * 局限：
 *   - Vercel Serverless 多实例下，每个实例独立 Map 不共享，因此真实限流可能为 N * maxPerMinute（N=实例数）
 *   - 当前 /api/waitlist/check 调用量小（B 端运营线 + 内测用户预校验），实例数有限，单实例 5/min 足够拦截滥用
 *   - 后续如需全局限流，可换 Upstash Redis；接口签名不变
 *
 * 内存清理：
 *   - 仅在被访问的 key 上做窗口清理；长期不被访问的 key 占用极小（仅一个空数组指针）
 *   - Serverless 冷启动会清空，无需额外回收
 */

type Bucket = {
  /** 升序的请求时间戳（ms） */
  timestamps: number[];
};

const WINDOW_MS = 60_000;

const store = new Map<string, Bucket>();

/**
 * 检查并记录一次访问。
 * @param key 限流 key（推荐 IP）
 * @param maxPerMinute 每分钟最大次数（含本次）
 * @returns true 放行，false 触发限流
 */
export function checkRateLimit(key: string, maxPerMinute: number): boolean {
  if (maxPerMinute <= 0) return false;
  const now = Date.now();
  const cutoff = now - WINDOW_MS;

  let bucket = store.get(key);
  if (!bucket) {
    bucket = { timestamps: [] };
    store.set(key, bucket);
  }

  // 移除窗口外（>= 60s 前）的时间戳
  // 时间戳数组升序，找到第一个 > cutoff 的位置一刀切
  let firstValid = 0;
  while (firstValid < bucket.timestamps.length && bucket.timestamps[firstValid] <= cutoff) {
    firstValid += 1;
  }
  if (firstValid > 0) {
    bucket.timestamps.splice(0, firstValid);
  }

  if (bucket.timestamps.length < maxPerMinute) {
    bucket.timestamps.push(now);
    return true;
  }

  return false;
}

/**
 * 仅供测试使用：清空所有限流状态。
 * 生产代码不应调用。
 */
export function __resetRateLimitForTests(): void {
  store.clear();
}
