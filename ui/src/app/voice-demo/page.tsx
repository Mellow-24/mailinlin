import type { Metadata } from "next";

import { YishuiVoiceDemo } from "./YishuiVoiceDemo";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "玲玲師傅（粵語）｜語音諮詢示範",
  description: "玲玲師傅香港風水粵語 AI 語音諮詢示範",
};

export default function VoiceDemoPage() {
  const embedToken = process.env.YISHUI_VOICE_DEMO_TOKEN?.trim() ?? "";

  return <YishuiVoiceDemo embedToken={embedToken} />;
}
