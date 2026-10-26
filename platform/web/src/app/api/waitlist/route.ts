import { NextRequest, NextResponse } from "next/server";
import { createServiceClient } from "@/lib/supabase-server";

// POST: 提交 Waitlist 申请
export async function POST(req: NextRequest) {
  let body: { email?: string; qq?: string; wechat?: string; message?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }

  const email = (body.email ?? "").trim();
  if (!email) {
    return NextResponse.json({ error: "email required" }, { status: 400 });
  }
  // 简单邮箱格式校验
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return NextResponse.json({ error: "invalid email" }, { status: 400 });
  }

  const qq = (body.qq ?? "").trim();
  const wechat = (body.wechat ?? "").trim();
  const message = (body.message ?? "").trim();

  const supabase = createServiceClient();

  // 入库
  const { error: dbErr } = await supabase.from("waitlist").insert({
    email,
    qq: qq || null,
    wechat: wechat || null,
    message: message || null,
  });
  if (dbErr) {
    return NextResponse.json({ error: dbErr.message }, { status: 500 });
  }

  return NextResponse.json({ success: true });
}

// GET: 查询 Waitlist 列表（Admin 用）
export async function GET(req: NextRequest) {
  const secret = (process.env.ADMIN_SECRET ?? "").trim();
  const fromX = (req.headers.get("x-admin-secret") ?? "").trim();
  const fromBearer = (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "").trim();
  const token = fromX || fromBearer;
  if (!secret || token !== secret) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const supabase = createServiceClient();
  const { data, error: dbErr } = await supabase
    .from("waitlist")
    .select("*")
    .order("created_at", { ascending: false });

  if (dbErr) {
    return NextResponse.json({ error: dbErr.message }, { status: 500 });
  }

  return NextResponse.json({ total: data?.length ?? 0, waitlist: data ?? [] });
}
