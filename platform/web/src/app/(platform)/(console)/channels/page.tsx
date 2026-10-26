import ComingSoon from "@/components/console/ComingSoon";
import { isChannelsUiEnabled } from "@/lib/feature-flags";
import { createServiceClient } from "@/lib/supabase-server";

export const dynamic = "force-dynamic";

export default async function ChannelsPage() {
  await isChannelsUiEnabled(createServiceClient());

  return <ComingSoon title="聊天方式" description="游戏之外的聊天方式还在准备中" icon="💬" />;
}
