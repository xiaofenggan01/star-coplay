"use client";

/**
 * 内测渠道入口（BetaChannelEntry）
 *
 * 场景：已通过运营审核（waitlist.status = approved）的用户在注册页快速领取邀请码。
 *
 * 流程：
 *   1. 用户点击触发按钮 → 弹出 Modal
 *   2. 用户填写 email / wechat / qq 中至少一项 → 提交
 *   3. POST /api/waitlist/check（精确匹配 + 服务端 5/min 限流）
 *   4. matched=true → onMatched({ waitlist_id, invite_code })，由父组件接管后续填充
 *      matched=false → 按 status 显示提示（not_found / not_approved）
 *
 * 限流：组件内前端层 30s 窗口（与服务端 5/min 限流共存，避免无效请求）。
 */

import { useEffect, useState } from "react";
import { validateEmailOptional } from "@/lib/register-validation";

const FRONTEND_RATE_LIMIT_MS = 30_000;

export interface BetaChannelMatchPayload {
  waitlist_id: string;
  invite_code: string;
}

interface BetaChannelEntryProps {
  /** 成功匹配并审核通过时的回调，父组件接管后续（自动填充邀请码 + 关闭 Modal） */
  onMatched: (payload: BetaChannelMatchPayload) => void;
  /** 触发按钮的样式覆写（不传则使用默认 link 样式） */
  triggerClassName?: string;
  /** 触发按钮的文案 */
  triggerLabel?: string;
}

interface CheckResponseSuccess {
  matched: true;
  waitlist_id: string;
  invite_code: string;
}

interface CheckResponseFail {
  matched: false;
  status?: "not_found" | "not_approved";
}

type CheckResponse = CheckResponseSuccess | CheckResponseFail;

export default function BetaChannelEntry({
  onMatched,
  triggerClassName,
  triggerLabel = "我已通过内测申请",
}: BetaChannelEntryProps) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [wechat, setWechat] = useState("");
  const [qq, setQQ] = useState("");
  const [emailError, setEmailError] = useState("");
  const [generalError, setGeneralError] = useState("");
  const [statusMsg, setStatusMsg] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [lastSubmitAt, setLastSubmitAt] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  // 倒计时刷新（仅在打开 Modal 且距上次提交 <30s 时启动）
  useEffect(() => {
    if (!open) return;
    const remaining = FRONTEND_RATE_LIMIT_MS - (now - lastSubmitAt);
    if (remaining <= 0) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [open, lastSubmitAt, now]);

  const cooldownRemainingMs = Math.max(0, FRONTEND_RATE_LIMIT_MS - (now - lastSubmitAt));
  const cooldownRemainingSec = Math.ceil(cooldownRemainingMs / 1000);
  const cooldownActive = cooldownRemainingMs > 0;

  function resetForm() {
    setEmail("");
    setWechat("");
    setQQ("");
    setEmailError("");
    setGeneralError("");
    setStatusMsg("");
  }

  function closeModal() {
    if (submitting) return;
    setOpen(false);
    resetForm();
  }

  async function handleSubmit() {
    setEmailError("");
    setGeneralError("");
    setStatusMsg("");

    const emailTrimmed = email.trim();
    const wechatTrimmed = wechat.trim();
    const qqTrimmed = qq.trim();

    if (!emailTrimmed && !wechatTrimmed && !qqTrimmed) {
      setGeneralError("请至少填写邮箱、微信号或 QQ 号中的一项");
      return;
    }

    if (emailTrimmed) {
      const err = validateEmailOptional(emailTrimmed);
      if (err) {
        setEmailError(err);
        return;
      }
    }

    if (cooldownActive) {
      setGeneralError(`请稍候 ${cooldownRemainingSec} 秒后再试`);
      return;
    }

    setSubmitting(true);
    setLastSubmitAt(Date.now());
    setNow(Date.now());

    try {
      const res = await fetch("/api/waitlist/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: emailTrimmed || undefined,
          wechat: wechatTrimmed || undefined,
          qq: qqTrimmed || undefined,
        }),
      });

      if (res.status === 429) {
        setGeneralError("请求过于频繁，请稍后再试");
        return;
      }

      if (!res.ok) {
        setGeneralError(`服务暂不可用（HTTP ${res.status}），请稍后重试`);
        return;
      }

      const data = (await res.json()) as CheckResponse;

      if (data.matched) {
        // 成功匹配：交还父组件，自身关闭
        onMatched({ waitlist_id: data.waitlist_id, invite_code: data.invite_code });
        setOpen(false);
        resetForm();
        return;
      }

      // 未命中：按 status 提示
      if (data.status === "not_approved") {
        setStatusMsg("资格待审核，运营会主动联系您");
      } else {
        setStatusMsg("未找到匹配记录，请确认填写的联系方式与申请时一致");
      }
    } catch {
      setGeneralError("网络错误，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  }

  const submitDisabled = submitting || cooldownActive;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={
          triggerClassName ??
          "text-[10px] text-stardew-blue hover:underline"
        }
      >
        {triggerLabel}
      </button>

      {open && (
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 backdrop-blur-sm px-4"
          onClick={closeModal}
        >
          <div
            className="parchment rounded-2xl p-6 max-w-sm w-full"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="font-[family-name:var(--font-pixel)] text-xs text-stardew-brown mb-1">
              内测渠道
            </h3>
            <p className="text-[10px] text-stardew-brown/60 mb-4">
              如果你已通过内测申请且审核通过，填写申请时使用的联系方式（任一即可），自动获取邀请码。
            </p>

            <div className="space-y-3">
              <div>
                <label className="block text-[10px] font-bold mb-1">邮箱</label>
                <input
                  type="email"
                  inputMode="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value);
                    if (emailError) setEmailError("");
                    if (statusMsg) setStatusMsg("");
                  }}
                  className={`w-full px-3 py-2 rounded-lg border-2 bg-white/60 text-sm focus:outline-none ${
                    emailError
                      ? "border-red-400 focus:border-red-500"
                      : "border-stardew-wood/30 focus:border-stardew-wood"
                  }`}
                  placeholder="申请时填写的邮箱"
                />
                {emailError && (
                  <p className="text-red-600 text-[10px] mt-1">{emailError}</p>
                )}
              </div>

              <div>
                <label className="block text-[10px] font-bold mb-1">微信号</label>
                <input
                  value={wechat}
                  onChange={(e) => {
                    setWechat(e.target.value);
                    if (statusMsg) setStatusMsg("");
                  }}
                  className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-sm focus:border-stardew-wood focus:outline-none"
                  placeholder="可选"
                />
              </div>

              <div>
                <label className="block text-[10px] font-bold mb-1">QQ 号</label>
                <input
                  value={qq}
                  onChange={(e) => {
                    setQQ(e.target.value);
                    if (statusMsg) setStatusMsg("");
                  }}
                  inputMode="numeric"
                  className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-sm focus:border-stardew-wood focus:outline-none"
                  placeholder="可选"
                />
              </div>

              {generalError && (
                <p className="text-red-600 text-[10px]">{generalError}</p>
              )}

              {statusMsg && (
                <p className="text-[10px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                  {statusMsg}
                </p>
              )}

              <button
                type="button"
                onClick={handleSubmit}
                disabled={submitDisabled}
                className="stardew-btn w-full py-2.5 rounded-lg font-bold text-xs disabled:opacity-50"
              >
                {submitting
                  ? "校验中..."
                  : cooldownActive
                  ? `请稍候 ${cooldownRemainingSec}s`
                  : "校验资格"}
              </button>

              <button
                type="button"
                onClick={closeModal}
                disabled={submitting}
                className="w-full py-1.5 text-[10px] text-stardew-brown/60 hover:text-stardew-brown disabled:opacity-50"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
