"use client";

import {
  AudioLines,
  CircleStop,
  Headphones,
  Loader2,
  MemoryStick,
  MessageCircleMore,
  Mic,
  PhoneCall,
  PhoneOff,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Volume2,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

type ConnectionStatus = "idle" | "connecting" | "connected" | "failed" | "ended";
type ConversationPhase =
  | "idle"
  | "connecting"
  | "greeting"
  | "ready"
  | "listening"
  | "processing"
  | "speaking"
  | "ended"
  | "error";

interface VoiceMessage {
  id: string;
  type: "user" | "assistant";
  role: "user" | "assistant";
  text: string;
  final: boolean;
  timestamp: string;
}

interface ClientOpening {
  assetId: string | null;
  audioUrl: string;
  transcript: string;
  durationMs: number | null;
}

// The widget's configured opening remains the permission/handshake contract.
// The demo surface selects one approved, static asset before a call starts so
// a visitor hears a natural variation without a first-turn TTS request.
const PRESET_CLIENT_OPENINGS: readonly ClientOpening[] = [
  {
    assetId: "greetings-01",
    audioUrl: "/voice-demo/warm-audio/greetings-01.wav",
    transcript:
      "你好呀，我係玲玲師傅 AI 語音角色。今日見到你好開心，想同你慢慢傾下。內容只作傳統文化同生活參考，今日想問咩呢？",
    durationMs: null,
  },
  {
    assetId: "greetings-02",
    audioUrl: "/voice-demo/warm-audio/greetings-02.wav",
    transcript:
      "新嘅一日開始啦，願你今日有好心情同一點小幸運。我係玲玲師傅 AI 語音角色，有咩想問，不妨慢慢講。",
    durationMs: null,
  },
  {
    assetId: "greetings-03",
    audioUrl: "/voice-demo/warm-audio/greetings-03.wav",
    transcript:
      "哈囉，見到你嚟咗，今日嘅氣氛都暖咗好多。我係玲玲師傅 AI 語音角色，想睇運程、家居風水，定係有其他心事想傾呢？",
    durationMs: null,
  },
  {
    assetId: "greetings-04",
    audioUrl: "/voice-demo/warm-audio/greetings-04.wav",
    transcript:
      "你好呀，今日本身就有一點小幸運等緊你。我係玲玲師傅 AI 語音角色，會用傳統文化同生活角度陪你分析，今日想從邊樣開始？",
    durationMs: null,
  },
  {
    assetId: "greetings-05",
    audioUrl: "/voice-demo/warm-audio/greetings-05.wav",
    transcript:
      "歡迎你嚟同玲玲師傅 AI 語音角色傾偈。無論你想問家居佈置、運程方向，定係近排有咩煩心事，都可以慢慢同我講。",
    durationMs: null,
  },
  {
    assetId: "greetings-06",
    audioUrl: "/voice-demo/warm-audio/greetings-06.wav",
    transcript:
      "今日天空好似特別靚，正好停一停，聽下自己心入面想問咩。我係玲玲師傅 AI 語音角色，今日想同你傾咩呢？",
    durationMs: null,
  },
  {
    assetId: "greetings-07",
    audioUrl: "/voice-demo/warm-audio/greetings-07.wav",
    transcript:
      "好耐冇傾都唔緊要，坐低飲杯茶，慢慢講。我係玲玲師傅 AI 語音角色，內容只作參考，你而家最想了解邊一方面？",
    durationMs: null,
  },
];

function selectDemoOpening(fallback: ClientOpening | null): ClientOpening | null {
  if (!fallback) return null;
  const index = Math.floor(Math.random() * PRESET_CLIENT_OPENINGS.length);
  return PRESET_CLIENT_OPENINGS[index] ?? PRESET_CLIENT_OPENINGS[0];
}

type OpeningPlaybackStatus =
  | "idle"
  | "preloaded"
  | "playing"
  | "blocked"
  | "failed"
  | "completed";

interface DograhVoiceWidget {
  init: () => Promise<void>;
  start: () => Promise<void>;
  stop: () => void;
  setContext: (context: Record<string, string>) => Record<string, string>;
  getState: () => {
    isInitialized: boolean;
    connectionStatus: string;
    voice: { isRecording: boolean; agentReady: boolean; botSpeaking?: boolean };
  };
  getVoiceMessages: () => VoiceMessage[];
  getOpeningTranscript: () => string | null;
  getClientOpening: () => ClientOpening | null;
  getOpeningAttemptId: () => number;
  completeClientOpening: (attemptId: number) => boolean;
  onVoiceMessage: (
    callback: (message: VoiceMessage, messages: VoiceMessage[]) => void,
  ) => void;
  onOpeningTranscript: (callback: (transcript: string) => void) => void;
  onAgentReady: (callback: (ready: boolean) => void) => void;
  onRecordingChange: (callback: (recording: boolean) => void) => void;
  onCallConnected: (callback: () => void) => void;
  onCallEnd: (callback: () => void) => void;
  onError: (callback: (error: unknown) => void) => void;
  onStatusChange: (callback: (status: string) => void) => void;
  startRecording: () => boolean;
  stopRecording: () => boolean;
  isRecording: () => boolean;
}

type VoiceDemoWindow = Window & { DograhWidget?: DograhVoiceWidget };

const VISITOR_STORAGE_KEY = "yishui_voice_visitor_id_v1";

const FEATURES = [
  { icon: MessageCircleMore, title: "即時雙向文字", detail: "一路講，一路顯示辨識結果同回覆" },
  { icon: Volume2, title: "粵語克隆聲線", detail: "語音同文字會同步回傳" },
  { icon: MemoryStick, title: "今次通話上下文", detail: "連續追問都會跟住前文" },
  { icon: ShieldCheck, title: "文化參考界線", detail: "唔會取代專業意見" },
] as const;

export const PROCESSING_STEPS = [
  {
    title: "辨識緊問事主題",
    detail: "分清生肖運程、八字、生辰、擇日或者家宅方向",
  },
  {
    title: "合參緊生肖同生辰線索",
    detail: "結合前文，核對你已提供嘅出生同近況資料",
  },
  {
    title: "查閱緊流年同傳統參考",
    detail: "配對知識庫入面相關嘅命理同風水內容",
  },
  {
    title: "歸納緊趨吉避忌建議",
    detail: "整理重點判斷同安全可行嘅行動方向",
  },
] as const;

export const PROCESSING_STEP_DELAYS_MS = [3_000, 7_000, 12_000] as const;

const WAVE_BARS = [
  { height: 18, delay: -680 },
  { height: 28, delay: -560 },
  { height: 42, delay: -440 },
  { height: 58, delay: -320 },
  { height: 74, delay: -200 },
  { height: 52, delay: -80 },
  { height: 36, delay: -440 },
  { height: 24, delay: -280 },
  { height: 36, delay: -440 },
  { height: 52, delay: -80 },
  { height: 74, delay: -200 },
  { height: 58, delay: -320 },
  { height: 42, delay: -440 },
  { height: 28, delay: -560 },
  { height: 18, delay: -680 },
] as const;

const PHASE_COPY: Record<ConversationPhase, { title: string; subtitle: string }> = {
  idle: { title: "準備好就可以開始", subtitle: "撳下面個按鈕，玲玲師傅會先同你打招呼" },
  connecting: { title: "接通緊", subtitle: "建立緊安全嘅即時語音連線" },
  greeting: { title: "玲玲師傅同你打緊招呼", subtitle: "請先聽完問候，之後錄音按鈕就會亮起" },
  ready: { title: "而家可以講嘢", subtitle: "撳咪高峰開始，講完再撳一次" },
  listening: { title: "聽緊你講", subtitle: "你可以自然啲講，講完再撳一次" },
  processing: {
    title: "合參緊今輪資料",
    subtitle: "結合問事主題、前文同傳統文化參考",
  },
  speaking: { title: "玲玲師傅回覆緊", subtitle: "播放語音嗰陣，文字會同步顯示" },
  ended: { title: "今次諮詢已經完結", subtitle: "你可以再開始一段新諮詢" },
  error: { title: "連線有啲問題", subtitle: "請檢查咪高峰權限同網絡，再試一次" },
};

function ProcessingIndicator({ stepIndex }: { stepIndex: number }) {
  const step = PROCESSING_STEPS[stepIndex] ?? PROCESSING_STEPS.at(-1);

  return (
    <div className="flex justify-start" aria-live="off">
      <div className="max-w-[92%] rounded-2xl rounded-bl-md border border-amber-200/15 bg-white/[0.04] px-4 py-3.5 sm:max-w-[78%]">
        <div className="flex items-center gap-3">
          <div className="relative flex size-11 shrink-0 items-center justify-center">
            <span className="absolute inset-0 rounded-full border border-amber-200/25 motion-safe:animate-ping" />
            <span className="absolute inset-1 rounded-full bg-amber-200/10" />
            <AudioLines className="relative size-5 text-amber-200" />
          </div>
          <div className="min-w-0 text-left">
            <p className="text-sm font-medium text-stone-200">玲玲師傅幫你合參緊</p>
            <p
              key={step?.title}
              className="mt-1 text-sm text-amber-100/85 motion-safe:animate-pulse"
            >
              {step?.title}
            </p>
            <p className="mt-0.5 text-xs leading-5 text-stone-500">{step?.detail}</p>
          </div>
        </div>
        <div className="mt-3 flex gap-1.5 border-t border-white/7 pt-3" aria-hidden="true">
          {PROCESSING_STEPS.map((item, index) => (
            <span
              key={item.title}
              className={cn(
                "h-1 flex-1 rounded-full transition-colors",
                index <= stepIndex ? "bg-amber-200/70" : "bg-white/8",
              )}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

interface VoiceStageProps {
  phase: ConversationPhase;
  recording: boolean;
  isProcessing: boolean;
  greetingPending: boolean;
  canRecord: boolean;
  processingStepIndex: number;
  onToggleRecording: () => void;
}

function VoiceStage({
  phase,
  recording,
  isProcessing,
  greetingPending,
  canRecord,
  processingStepIndex,
  onToggleRecording,
}: VoiceStageProps) {
  const visualPhase = recording ? "listening" : isProcessing ? "processing" : phase;
  const processingStep =
    PROCESSING_STEPS[processingStepIndex] ?? PROCESSING_STEPS.at(-1);
  const showRecordingControl = canRecord || recording;
  const helperText = recording
    ? "再撳一次，結束今輪講話"
    : isProcessing
      ? processingStep?.title
      : phase === "speaking"
        ? "粵語回覆播放緊"
        : greetingPending || phase === "greeting"
          ? "開場白播放緊"
          : phase === "connecting"
            ? "建立緊語音連線"
            : "撳一下開始講";
  const buttonLabel = recording ? "結束今輪錄音" : "開始今輪錄音";

  return (
    <div className="mt-3 flex w-full flex-col items-center">
      <div
        className="yishui-voice-stage relative flex h-40 w-full max-w-md items-center justify-center overflow-hidden"
        data-phase={visualPhase}
      >
        <div
          className="absolute inset-x-5 top-1/2 flex -translate-y-1/2 items-center justify-center gap-1.5 sm:gap-2"
          aria-hidden="true"
        >
          {WAVE_BARS.map(({ height, delay }, index) => (
            <span
              key={`${height}-${index}`}
              className="yishui-wave-bar w-1 rounded-full bg-amber-200/55 sm:w-1.5"
              style={{ height, animationDelay: `${delay}ms` }}
            />
          ))}
        </div>

        {isProcessing ? (
          <div className="absolute inset-0 flex items-center justify-center" aria-hidden="true">
            <span className="yishui-thinking-ring absolute size-24 rounded-full border border-amber-200/20" />
            <span className="yishui-thinking-ring absolute size-24 rounded-full border border-amber-200/15 [animation-delay:650ms]" />
          </div>
        ) : null}

        {showRecordingControl ? (
          <Button
            type="button"
            onClick={onToggleRecording}
            aria-label={buttonLabel}
            aria-pressed={recording}
            className={cn(
              "relative z-10 size-24 rounded-full border p-0 transition-transform focus-visible:ring-4 focus-visible:ring-amber-200/25",
              recording
                ? "border-red-300/50 bg-red-400 text-white shadow-[0_0_0_12px_rgba(248,113,113,.08)] hover:bg-red-400"
                : "border-amber-200/35 bg-amber-200 text-stone-950 shadow-[0_12px_45px_rgba(229,192,107,.2)] hover:scale-[1.03] hover:bg-amber-100",
            )}
          >
            {recording ? <CircleStop className="size-9" /> : <Mic className="size-9" />}
          </Button>
        ) : (
          <div
            className="relative z-10 flex size-20 items-center justify-center rounded-full border border-amber-200/15 bg-[#1d1c14] text-amber-100"
            aria-hidden="true"
          >
            {phase === "speaking" ? (
              <Volume2 className="size-8 motion-safe:animate-pulse" />
            ) : phase === "connecting" ? (
              <Loader2 className="size-8 motion-safe:animate-spin" />
            ) : greetingPending || phase === "greeting" ? (
              <Headphones className="size-8 motion-safe:animate-pulse" />
            ) : (
              <AudioLines className="size-8 motion-safe:animate-pulse" />
            )}
          </div>
        )}
      </div>
      <p className="-mt-1 text-xs text-stone-500">{helperText}</p>
    </div>
  );
}

function getOrCreateVisitorId() {
  const existing = window.localStorage.getItem(VISITOR_STORAGE_KEY);
  if (existing) return existing;
  const id = window.crypto.randomUUID();
  window.localStorage.setItem(VISITOR_STORAGE_KEY, id);
  return id;
}

async function resolvePublicApiEndpoint() {
  try {
    const response = await fetch("/api/config/version", { cache: "no-store" });
    if (response.ok) {
      const data = (await response.json()) as { backendApiEndpoint?: string | null };
      if (data.backendApiEndpoint) return data.backendApiEndpoint;
    }
  } catch {
    // The connection error is surfaced by the widget with a customer-safe message.
  }

  if (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1") {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return window.location.origin;
}

function loadVoiceWidget(embedToken: string, apiEndpoint: string) {
  const voiceWindow = window as VoiceDemoWindow;
  if (voiceWindow.DograhWidget) return Promise.resolve(voiceWindow.DograhWidget);

  return new Promise<DograhVoiceWidget>((resolve, reject) => {
    const script = document.createElement("script");
    const src = new URL("/embed/dograh-widget.js", window.location.origin);
    src.searchParams.set("token", embedToken);
    src.searchParams.set("apiEndpoint", apiEndpoint);
    script.src = src.toString();
    script.async = true;
    script.dataset.yishuiVoiceWidget = "true";
    script.onload = () => {
      if (voiceWindow.DograhWidget) resolve(voiceWindow.DograhWidget);
      else reject(new Error("語音元件未能完成初始化"));
    };
    script.onerror = () => reject(new Error("語音元件載入失敗"));
    document.body.appendChild(script);
  });
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "暫時未能連接玲玲師傅";
}

function localizeVoiceMessage(message: VoiceMessage): VoiceMessage {
  if (message.type === "user" && message.text === "正在聆听…") {
    return { ...message, text: "聽緊你講…" };
  }
  return { ...message };
}

export function YishuiVoiceDemo({ embedToken }: { embedToken: string }) {
  useLayoutEffect(() => {
    const root = document.documentElement;
    const hideSupportLauncher = () => {
      window.$chatwoot?.toggle?.("close");
      window.$chatwoot?.toggleBubbleVisibility?.("hide");
    };

    root.classList.add("yishui-voice-demo-page");
    hideSupportLauncher();
    window.addEventListener("chatwoot:ready", hideSupportLauncher);

    return () => {
      root.classList.remove("yishui-voice-demo-page");
      window.removeEventListener("chatwoot:ready", hideSupportLauncher);
    };
  }, []);

  const widgetRef = useRef<DograhVoiceWidget | null>(null);
  const messageEndRef = useRef<HTMLDivElement | null>(null);
  const openingAudioRef = useRef<HTMLAudioElement | null>(null);
  const clientOpeningRef = useRef<ClientOpening | null>(null);
  const preconnectedOpeningTranscriptRef = useRef<string | null>(null);
  const recordingRef = useRef(false);
  const greetingPendingRef = useRef(false);
  const greetingAssistantIdRef = useRef<string | null>(null);
  const processingTurnRef = useRef(0);
  const openingAttemptRef = useRef(false);
  const openingAttemptIdRef = useRef<number | null>(null);
  const consultationStartedRef = useRef(false);
  const preconnectPromiseRef = useRef<Promise<void> | null>(null);
  const [widgetReady, setWidgetReady] = useState(false);
  const [consultationStarted, setConsultationStarted] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("idle");
  const [phase, setPhase] = useState<ConversationPhase>("idle");
  const [messages, setMessages] = useState<VoiceMessage[]>([]);
  const [openingTranscript, setOpeningTranscript] = useState<string | null>(null);
  const [clientOpening, setClientOpening] = useState<ClientOpening | null>(null);
  const [openingPlaybackStatus, setOpeningPlaybackStatus] =
    useState<OpeningPlaybackStatus>("idle");
  const [openingPlaybackNotice, setOpeningPlaybackNotice] = useState<string | null>(null);
  const [openingTranscriptStreamed, setOpeningTranscriptStreamed] = useState(false);
  const [greetingPending, setGreetingPending] = useState(false);
  const [agentReady, setAgentReady] = useState(false);
  const [recording, setRecording] = useState(false);
  const [processingStepIndex, setProcessingStepIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const isProcessing =
    phase === "processing" && connectionStatus === "connected" && !recording;

  const stopOpeningAudio = useCallback(() => {
    const audio = openingAudioRef.current;
    if (!audio) return;
    audio.pause();
    audio.currentTime = 0;
  }, []);

  const completeOpeningAttempt = useCallback(
    (
      attemptId: number | null,
      status: Extract<OpeningPlaybackStatus, "completed" | "failed">,
      notice: string | null,
    ) => {
      if (
        attemptId === null ||
        !openingAttemptRef.current ||
        openingAttemptIdRef.current !== attemptId
      ) {
        return false;
      }
      openingAttemptRef.current = false;
      setOpeningPlaybackStatus(status);
      setOpeningPlaybackNotice(notice);
      widgetRef.current?.completeClientOpening(attemptId);
      return true;
    },
    [],
  );

  const completeOpeningWithoutAudio = useCallback(
    (notice: string) => {
      completeOpeningAttempt(openingAttemptIdRef.current, "failed", notice);
    },
    [completeOpeningAttempt],
  );

  const playOpeningAudio = useCallback(async (attemptId: number) => {
    const audio = openingAudioRef.current;
    if (!audio || !clientOpening) return false;
    setOpeningPlaybackNotice(null);
    try {
      audio.dataset.openingAttemptId = String(attemptId);
      audio.currentTime = 0;
      const playback = audio.play();
      setOpeningPlaybackStatus("playing");
      await playback;
      return true;
    } catch {
      if (audio.error) {
        completeOpeningWithoutAudio(
          "開場白未能播放，文字提示已保留，你仍然可以繼續諮詢。",
        );
        return false;
      }
      setOpeningPlaybackStatus("blocked");
      setOpeningPlaybackNotice("瀏覽器暫停咗開場白，請撳下面個按鈕開啟聲音。");
      return false;
    }
  }, [clientOpening, completeOpeningWithoutAudio]);

  const handleOpeningEnded = useCallback(
    (attemptId: number) => {
      completeOpeningAttempt(attemptId, "completed", null);
    },
    [completeOpeningAttempt],
  );

  const handleOpeningError = useCallback(
    (attemptId: number | null) => {
      const currentAttemptId = openingAttemptIdRef.current;
      const activeAttemptId = attemptId ?? currentAttemptId;
      if (
        completeOpeningAttempt(
          activeAttemptId,
          "failed",
          "開場白未能播放，文字提示已保留，你仍然可以繼續諮詢。",
        )
      ) {
        return;
      }

      // Ignore a late media error for an attempt that already ended or failed.
      if (currentAttemptId !== null && activeAttemptId === currentAttemptId) return;

      setOpeningPlaybackStatus("failed");
      setOpeningPlaybackNotice("開場白資源暫時未能載入。");
    },
    [completeOpeningAttempt],
  );

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ behavior: "auto", block: "nearest" });
  }, [messages, openingTranscript, phase]);

  useEffect(() => {
    if (!isProcessing) return;
    const turn = processingTurnRef.current;
    setProcessingStepIndex(0);
    const timers = PROCESSING_STEP_DELAYS_MS.map((delay, index) =>
      window.setTimeout(() => {
        if (processingTurnRef.current === turn) setProcessingStepIndex(index + 1);
      }, delay),
    );
    return () => timers.forEach(window.clearTimeout);
  }, [isProcessing]);

  useEffect(() => {
    if (!embedToken) return;
    let cancelled = false;
    let activeWidget: DograhVoiceWidget | null = null;

    const boot = async () => {
      try {
        const apiEndpoint = await resolvePublicApiEndpoint();
        const widget = await loadVoiceWidget(embedToken, apiEndpoint);
        await widget.init();
        if (cancelled) return;

        activeWidget = widget;
        widgetRef.current = widget;
        const opening = selectDemoOpening(widget.getClientOpening());
        clientOpeningRef.current = opening;
        setClientOpening(opening);
        setOpeningPlaybackStatus(opening ? "preloaded" : "idle");
        widget.setContext({
          visitor_id: getOrCreateVisitorId(),
          client_surface: "yishui_voice_demo",
        });
        widget.onOpeningTranscript((transcript) => {
          preconnectedOpeningTranscriptRef.current = transcript;
          if (cancelled || !consultationStartedRef.current) return;
          setOpeningTranscript(clientOpeningRef.current?.transcript ?? transcript);
          setOpeningTranscriptStreamed(false);
          greetingAssistantIdRef.current = null;
          greetingPendingRef.current = true;
          setGreetingPending(true);
        });
        widget.onVoiceMessage((message, allMessages) => {
          if (cancelled) return;
          setMessages(allMessages.map(localizeVoiceMessage));
          if (message.type === "user") {
            if (!widget.getState().voice.botSpeaking) {
              setPhase(
                message.final || !recordingRef.current ? "processing" : "listening",
              );
            }
          } else if (greetingPendingRef.current) {
            greetingAssistantIdRef.current = message.id;
            setOpeningTranscriptStreamed(true);
            setPhase("greeting");
          } else {
            setPhase(message.final ? "ready" : "speaking");
          }
        });
        widget.onAgentReady((ready) => {
          if (cancelled) return;
          setAgentReady(ready);
          if (ready && consultationStartedRef.current) {
            greetingPendingRef.current = false;
            setGreetingPending(false);
            setPhase("ready");
          }
        });
        widget.onRecordingChange((isRecording) => {
          if (cancelled) return;
          recordingRef.current = isRecording;
          setRecording(isRecording);
          if (isRecording) {
            processingTurnRef.current += 1;
            setProcessingStepIndex(0);
          }
          setPhase((current) => {
            if (isRecording) return "listening";
            return current === "listening" ? "processing" : current;
          });
        });
        widget.onCallConnected(() => {
          if (cancelled) return;
          setConnectionStatus("connected");
          if (consultationStartedRef.current) {
            setPhase(widget.getState().voice.agentReady ? "ready" : "greeting");
          }
        });
        widget.onCallEnd(() => {
          if (cancelled) return;
          preconnectPromiseRef.current = null;
          openingAttemptRef.current = false;
          openingAttemptIdRef.current = null;
          stopOpeningAudio();
          setConnectionStatus(
            consultationStartedRef.current ? "ended" : "idle",
          );
          recordingRef.current = false;
          greetingPendingRef.current = false;
          setRecording(false);
          setAgentReady(false);
          setGreetingPending(false);
          setPhase(consultationStartedRef.current ? "ended" : "idle");
        });
        widget.onStatusChange((status) => {
          if (cancelled) return;
          if (status === "connecting") {
            setConnectionStatus("connecting");
            if (consultationStartedRef.current) setPhase("connecting");
          } else if (status === "connected") {
            setConnectionStatus("connected");
          } else if (status === "failed") {
            openingAttemptRef.current = false;
            openingAttemptIdRef.current = null;
            stopOpeningAudio();
            recordingRef.current = false;
            greetingPendingRef.current = false;
            setConnectionStatus(
              consultationStartedRef.current ? "failed" : "idle",
            );
            setGreetingPending(false);
            setPhase(consultationStartedRef.current ? "error" : "idle");
          }
        });
        widget.onError((widgetError) => {
          if (cancelled) return;
          openingAttemptRef.current = false;
          openingAttemptIdRef.current = null;
          stopOpeningAudio();
          recordingRef.current = false;
          greetingPendingRef.current = false;
          if (consultationStartedRef.current) {
            setError(errorMessage(widgetError));
          }
          setConnectionStatus(
            consultationStartedRef.current ? "failed" : "idle",
          );
          setGreetingPending(false);
          setPhase(consultationStartedRef.current ? "error" : "idle");
        });
        setWidgetReady(true);
        const preconnectPromise = widget.start();
        preconnectPromiseRef.current = preconnectPromise;
        void preconnectPromise.catch((preconnectError) => {
          if (cancelled) return;
          if (preconnectPromiseRef.current === preconnectPromise) {
            preconnectPromiseRef.current = null;
          }
          if (consultationStartedRef.current) {
            setError(errorMessage(preconnectError));
            setConnectionStatus("failed");
            setPhase("error");
          } else {
            setConnectionStatus("idle");
            setPhase("idle");
          }
        });
      } catch (bootError) {
        if (!cancelled) {
          setError(errorMessage(bootError));
          setConnectionStatus("failed");
          setPhase("error");
        }
      }
    };

    void boot();
    return () => {
      cancelled = true;
      consultationStartedRef.current = false;
      preconnectPromiseRef.current = null;
      openingAttemptRef.current = false;
      openingAttemptIdRef.current = null;
      stopOpeningAudio();
      if (["connected", "connecting"].includes(activeWidget?.getState().connectionStatus ?? "")) {
        activeWidget?.stop();
      }
      if (widgetRef.current === activeWidget) widgetRef.current = null;
    };
  }, [embedToken, stopOpeningAudio]);

  const startConsultation = useCallback(async () => {
    const widget = widgetRef.current;
    if (!widget) return;
    consultationStartedRef.current = true;
    setConsultationStarted(true);
    stopOpeningAudio();
    const opening = clientOpeningRef.current;
    openingAttemptRef.current = Boolean(opening);
    setMessages([]);
    setOpeningTranscript(
      opening?.transcript ?? preconnectedOpeningTranscriptRef.current,
    );
    setOpeningTranscriptStreamed(false);
    greetingAssistantIdRef.current = null;
    greetingPendingRef.current = true;
    setGreetingPending(true);
    setError(null);
    setAgentReady(false);
    recordingRef.current = false;
    processingTurnRef.current += 1;
    setProcessingStepIndex(0);
    setRecording(false);
    const currentConnectionStatus = widget.getState().connectionStatus;
    const connectionIsWarm = ["connecting", "connected"].includes(
      currentConnectionStatus,
    );
    setConnectionStatus(
      connectionIsWarm
        ? (currentConnectionStatus as ConnectionStatus)
        : "connecting",
    );
    setPhase(currentConnectionStatus === "connected" ? "greeting" : "connecting");
    setOpeningPlaybackStatus(opening ? "preloaded" : "idle");
    setOpeningPlaybackNotice(null);
    try {
      let startPromise = preconnectPromiseRef.current;
      if (!connectionIsWarm) {
        widget.setContext({
          visitor_id: getOrCreateVisitorId(),
          client_surface: "yishui_voice_demo",
        });
        startPromise = widget.start();
        preconnectPromiseRef.current = startPromise;
      }
      const openingAttemptId = widget.getOpeningAttemptId();
      openingAttemptIdRef.current = opening ? openingAttemptId : null;
      if (opening) void playOpeningAudio(openingAttemptId);
      if (startPromise) await startPromise;
    } catch (startError) {
      openingAttemptRef.current = false;
      openingAttemptIdRef.current = null;
      stopOpeningAudio();
      greetingPendingRef.current = false;
      setGreetingPending(false);
      setError(errorMessage(startError));
      setConnectionStatus("failed");
      setPhase("error");
    }
  }, [playOpeningAudio, stopOpeningAudio]);

  const toggleRecording = useCallback(() => {
    const widget = widgetRef.current;
    if (!widget) return;
    if (widget.isRecording()) {
      widget.stopRecording();
      setPhase("processing");
      return;
    }
    if (!widget.startRecording()) {
      setError("請等玲玲師傅講完開場白先開始講嘢");
    } else {
      setError(null);
    }
  }, []);

  const endConsultation = useCallback(() => {
    widgetRef.current?.stop();
  }, []);

  const phaseCopy = PHASE_COPY[phase];
  const callActive =
    consultationStarted &&
    (connectionStatus === "connecting" || connectionStatus === "connected");
  const showOpeningTranscript = Boolean(openingTranscript && !openingTranscriptStreamed);
  const hasTranscriptContent = showOpeningTranscript || messages.length > 0 || isProcessing;
  const latestMessage = messages.at(-1);
  const liveTranscriptText = latestMessage?.final
    ? `${latestMessage.type === "user" ? "你" : "玲玲師傅"}：${latestMessage.text}`
    : showOpeningTranscript
      ? `開場字幕：${openingTranscript}`
      : "";
  const canRecord =
    connectionStatus === "connected" &&
    agentReady &&
    (phase === "ready" || recording);

  return (
    <main lang="zh-HK" className="relative min-h-svh overflow-hidden bg-[#070807] text-stone-100">
      {clientOpening ? (
        <audio
          ref={openingAudioRef}
          src={clientOpening.audioUrl}
          preload="auto"
          className="hidden"
          aria-hidden="true"
          onCanPlay={() => {
            setOpeningPlaybackStatus((current) =>
              current === "idle" ? "preloaded" : current,
            );
          }}
          onPlaying={(event) => {
            const attemptId = Number(event.currentTarget.dataset.openingAttemptId);
            if (openingAttemptIdRef.current === attemptId) {
              setOpeningPlaybackStatus("playing");
              setPhase("greeting");
            }
          }}
          onEnded={(event) => {
            handleOpeningEnded(Number(event.currentTarget.dataset.openingAttemptId));
          }}
          onError={(event) => {
            const rawAttemptId = event.currentTarget.dataset.openingAttemptId;
            handleOpeningError(rawAttemptId ? Number(rawAttemptId) : null);
          }}
        />
      ) : null}
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_16%_12%,rgba(180,135,52,0.16),transparent_30%),radial-gradient(circle_at_85%_82%,rgba(65,91,69,0.18),transparent_32%)]" />
      <div className="pointer-events-none absolute inset-0 opacity-[0.035] [background-image:linear-gradient(rgba(255,255,255,.7)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,.7)_1px,transparent_1px)] [background-size:48px_48px]" />

      <div className="relative mx-auto flex min-h-svh w-full max-w-7xl flex-col px-4 py-5 sm:px-6 lg:px-8">
        <header className="flex items-center justify-between gap-4 border-b border-amber-200/10 pb-4">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex size-11 shrink-0 items-center justify-center rounded-full border border-amber-300/25 bg-amber-300/10 font-serif text-xl text-amber-200">
              玲
            </div>
            <div className="min-w-0">
              <p className="truncate text-base font-semibold tracking-[0.18em] text-amber-100">玲玲師傅</p>
              <p className="truncate text-xs text-stone-400">香港風水 · 粵語語音諮詢</p>
            </div>
          </div>
          <Badge
            variant="outline"
            className="border-emerald-300/20 bg-emerald-300/5 text-emerald-200"
          >
            <span className="mr-1.5 size-1.5 rounded-full bg-emerald-300" />
            AI 角色示範
          </Badge>
        </header>

        <div className="grid min-h-0 flex-1 gap-5 py-5 lg:grid-cols-[310px_minmax(0,1fr)]">
          <aside className="hidden space-y-4 lg:block">
            <Card className="border-amber-200/10 bg-[#11130f]/90">
              <CardHeader>
                <div className="mb-3 flex size-14 items-center justify-center rounded-2xl border border-amber-300/20 bg-amber-300/10">
                  <Sparkles className="size-7 text-amber-200" />
                </div>
                <CardTitle className="text-lg text-stone-100">香港風水師 AI 語音角色</CardTitle>
                <CardDescription className="leading-6 text-stone-400">
                  結合今次對話嘅前文，提供家居佈置、生活環境同一般傳統文化參考。
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <Separator className="bg-white/8" />
                {FEATURES.map(({ icon: FeatureIcon, title, detail }) => {
                  return (
                    <div key={title} className="flex gap-3">
                      <FeatureIcon className="mt-0.5 size-4 shrink-0 text-amber-200/80" />
                      <div>
                        <p className="text-sm font-medium text-stone-200">{title}</p>
                        <p className="mt-0.5 text-xs leading-5 text-stone-500">{detail}</p>
                      </div>
                    </div>
                  );
                })}
              </CardContent>
            </Card>

            <div className="rounded-xl border border-white/8 bg-white/[0.025] p-4 text-xs leading-5 text-stone-500">
              呢個係以香港風水師麥玲玲為角色設定嘅 AI 語音體驗，唔係麥玲玲本人。內容只供傳統文化交流同一般生活參考。
            </div>
          </aside>

          <Card className="flex min-h-[72svh] flex-col overflow-hidden border-amber-100/10 bg-[#0d0f0c]/95 shadow-2xl shadow-black/30 lg:min-h-0">
            <CardHeader className="border-b border-white/8 px-4 py-4 sm:px-6">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <CardTitle className="text-base text-stone-100">即時諮詢紀錄</CardTitle>
                  <CardDescription className="mt-1 text-xs text-stone-500">
                    語音辨識同玲玲師傅嘅回覆會同步顯示
                  </CardDescription>
                </div>
                <div className="flex items-center gap-2 text-xs text-stone-400" aria-live="polite">
                  <span
                    className={cn(
                      "size-2 rounded-full",
                      connectionStatus === "connected"
                        ? "bg-emerald-400"
                        : connectionStatus === "connecting"
                          ? "bg-amber-300 motion-safe:animate-pulse"
                          : connectionStatus === "failed"
                            ? "bg-red-400"
                            : "bg-stone-600",
                    )}
                  />
                  {connectionStatus === "connected"
                    ? consultationStarted
                      ? "已連線"
                      : "語音已預熱"
                    : connectionStatus === "connecting"
                      ? consultationStarted
                        ? "連線緊"
                        : "背景預熱緊"
                      : connectionStatus === "failed"
                        ? "連線失敗"
                        : "未連線"}
                </div>
              </div>
            </CardHeader>

            <CardContent className="flex min-h-0 flex-1 flex-col p-0">
              <div
                className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-5 sm:px-6"
                role="log"
                aria-label="語音諮詢文字紀錄"
                aria-live="off"
              >
                {!hasTranscriptContent ? (
                  <div className="flex h-full min-h-64 flex-col items-center justify-center text-center">
                    <div className="mb-4 flex size-14 items-center justify-center rounded-full border border-white/10 bg-white/[0.03]">
                      <Headphones className="size-6 text-stone-400" />
                    </div>
                    <p className="text-sm font-medium text-stone-300">
                      {callActive ? "準備緊開場白…" : "諮詢仲未開始"}
                    </p>
                    <p className="mt-2 max-w-sm text-xs leading-5 text-stone-500">
                      開始之後，玲玲師傅會先同你打招呼。聽完開場白，再撳咪高峰講出你想問嘅問題。
                    </p>
                  </div>
                ) : (
                  <>
                    {showOpeningTranscript ? (
                      <div className="flex justify-start">
                        <div className="max-w-[88%] rounded-2xl rounded-bl-md border border-amber-200/15 bg-amber-100/[0.055] px-4 py-3 text-sm leading-6 text-stone-200 sm:max-w-[76%]">
                          <div className="mb-1 flex items-center gap-2 text-[11px] font-medium text-amber-200/65">
                            開場字幕
                            {openingPlaybackStatus === "playing" ? (
                              <span className="motion-safe:animate-pulse">播放緊</span>
                            ) : greetingPending ? (
                              <span className="motion-safe:animate-pulse">就快播放</span>
                            ) : null}
                          </div>
                          <p>{openingTranscript}</p>
                        </div>
                      </div>
                    ) : null}

                    {messages.map((message) => (
                      <div
                        key={message.id}
                        className={cn(
                          "flex",
                          message.type === "user" ? "justify-end" : "justify-start",
                        )}
                      >
                        <div
                          className={cn(
                            "max-w-[88%] rounded-2xl px-4 py-3 text-sm leading-6 sm:max-w-[76%]",
                            message.type === "user"
                              ? "rounded-br-md bg-amber-200 text-stone-950"
                              : "rounded-bl-md border border-white/8 bg-white/[0.045] text-stone-200",
                            !message.final && "opacity-75",
                          )}
                        >
                          <div className="mb-1 flex items-center gap-2 text-[11px] font-medium opacity-65">
                            {message.type === "user"
                              ? "你"
                              : message.id === greetingAssistantIdRef.current
                                ? "開場字幕"
                                : "玲玲師傅"}
                            {!message.final ? (
                              <span className="motion-safe:animate-pulse">即時輸入緊</span>
                            ) : null}
                          </div>
                          <p>{message.text}</p>
                        </div>
                      </div>
                    ))}

                    {isProcessing ? (
                      <ProcessingIndicator stepIndex={processingStepIndex} />
                    ) : null}
                  </>
                )}
                <p className="sr-only" aria-live="polite" aria-atomic="true">
                  {liveTranscriptText}
                </p>
                <div ref={messageEndRef} />
              </div>

              <div className="border-t border-white/8 bg-black/15 px-4 py-5 sm:px-6">
                <div className="mx-auto flex max-w-xl flex-col items-center text-center">
                  <div
                    className="min-h-12"
                    role="status"
                    aria-live="polite"
                    aria-atomic="true"
                  >
                    <p className="text-sm font-medium text-stone-200">{phaseCopy.title}</p>
                    <p className="mt-1 text-xs text-stone-500">{phaseCopy.subtitle}</p>
                  </div>

                  {!embedToken ? (
                    <div className="mt-4 w-full rounded-lg border border-red-400/20 bg-red-400/5 p-3 text-sm text-red-200">
                      示範 Token 仲未設定，請先執行本機設定程式。
                    </div>
                  ) : error ? (
                    <div className="mt-4 w-full rounded-lg border border-red-400/20 bg-red-400/5 p-3 text-sm text-red-200">
                      {error}
                    </div>
                  ) : null}

                  {openingPlaybackNotice ? (
                    <div className="mt-3 w-full rounded-lg border border-amber-300/15 bg-amber-200/5 p-3 text-sm text-amber-100/80">
                      {openingPlaybackNotice}
                    </div>
                  ) : null}

                  {callActive && openingPlaybackStatus === "blocked" ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        const attemptId = openingAttemptIdRef.current;
                        if (attemptId !== null) void playOpeningAudio(attemptId);
                      }}
                      className="mt-3 border-amber-200/25 bg-amber-200/5 text-amber-100 hover:bg-amber-200/10"
                    >
                      <Volume2 className="size-4" />
                      撳一下播放開場白
                    </Button>
                  ) : null}

                  {!callActive ? (
                    <Button
                      size="lg"
                      onClick={() => void startConsultation()}
                      disabled={!embedToken || !widgetReady}
                      className="mt-5 h-12 rounded-full bg-amber-200 px-7 text-stone-950 hover:bg-amber-100"
                    >
                      {!widgetReady ? (
                        <Loader2 className="size-5 motion-safe:animate-spin" />
                      ) : connectionStatus === "failed" || connectionStatus === "ended" ? (
                        <RefreshCw className="size-5" />
                      ) : (
                        <PhoneCall className="size-5" />
                      )}
                      {connectionStatus === "failed" || connectionStatus === "ended"
                        ? "重新開始諮詢"
                        : "開始語音諮詢"}
                    </Button>
                  ) : (
                    <div className="mt-1 flex w-full flex-col items-center gap-3">
                      <VoiceStage
                        phase={phase}
                        recording={recording}
                        isProcessing={isProcessing}
                        greetingPending={greetingPending}
                        canRecord={canRecord}
                        processingStepIndex={processingStepIndex}
                        onToggleRecording={toggleRecording}
                      />
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={endConsultation}
                        className="text-stone-500 hover:bg-white/5 hover:text-stone-300"
                      >
                        <PhoneOff className="size-4" />
                        結束今次諮詢
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        <footer className="flex flex-col items-center justify-between gap-2 border-t border-amber-200/10 pt-4 text-[11px] text-stone-600 sm:flex-row">
          <span className="flex items-center gap-1.5">
            <AudioLines className="size-3.5" />
            AI 即時語音體驗
          </span>
          <span>請勿提供完整住址、帳戶或者其他不必要嘅敏感資料</span>
        </footer>
      </div>
    </main>
  );
}
