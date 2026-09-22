import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  PROCESSING_STEP_DELAYS_MS,
  PROCESSING_STEPS,
  YishuiVoiceDemo,
} from "./YishuiVoiceDemo";

describe("YishuiVoiceDemo", () => {
  let recording = false;
  let openingAttemptId = 0;
  let openingTranscriptCallback: (transcript: string) => void = () => undefined;
  let voiceMessageCallback: (
    message: {
      id: string;
      type: "user" | "assistant";
      role: "user" | "assistant";
      text: string;
      final: boolean;
      timestamp: string;
    },
    messages: never[],
  ) => void = () => undefined;
  let agentReadyCallback: (ready: boolean) => void = () => undefined;
  let recordingCallback: (active: boolean) => void = () => undefined;
  let connectedCallback: () => void = () => undefined;

  const widget = {
    init: vi.fn().mockResolvedValue(undefined),
    start: vi.fn(async () => {
      openingAttemptId += 1;
      openingTranscriptCallback("你好，歡迎嚟到玲玲師傅。今日想問咩呢？");
      connectedCallback();
    }),
    stop: vi.fn(),
    setContext: vi.fn((context: Record<string, string>) => context),
    getState: vi.fn(() => ({
      isInitialized: true,
      connectionStatus: "connected",
      voice: { isRecording: recording, agentReady: true },
    })),
    getVoiceMessages: vi.fn(() => []),
    getOpeningTranscript: vi.fn(() => null),
    getClientOpening: vi.fn(
      (): {
        assetId: string | null;
        audioUrl: string;
        transcript: string;
        durationMs: number | null;
      } | null => null,
    ),
    getOpeningAttemptId: vi.fn(() => openingAttemptId),
    completeClientOpening: vi.fn((attemptId: number) => attemptId === openingAttemptId),
    onVoiceMessage: vi.fn((callback: typeof voiceMessageCallback) => {
      voiceMessageCallback = callback;
    }),
    onOpeningTranscript: vi.fn((callback: typeof openingTranscriptCallback) => {
      openingTranscriptCallback = callback;
    }),
    onAgentReady: vi.fn((callback: typeof agentReadyCallback) => {
      agentReadyCallback = callback;
    }),
    onRecordingChange: vi.fn((callback: typeof recordingCallback) => {
      recordingCallback = callback;
    }),
    onCallConnected: vi.fn((callback: typeof connectedCallback) => {
      connectedCallback = callback;
    }),
    onCallEnd: vi.fn(),
    onError: vi.fn(),
    onStatusChange: vi.fn(),
    startRecording: vi.fn(() => {
      recording = true;
      recordingCallback(true);
      return true;
    }),
    stopRecording: vi.fn(() => {
      recording = false;
      recordingCallback(false);
      return true;
    }),
    isRecording: vi.fn(() => recording),
  };

  beforeEach(() => {
    recording = false;
    openingAttemptId = 0;
    widget.getClientOpening.mockReturnValue(null);
    window.localStorage.setItem("yishui_voice_visitor_id_v1", "visitor-test");
    Object.defineProperty(window, "DograhWidget", {
      configurable: true,
      writable: true,
      value: widget,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ backendApiEndpoint: "http://localhost:8000" }),
      }),
    );
    Element.prototype.scrollIntoView = vi.fn();
  });

  afterEach(() => {
    delete (window as Window & { DograhWidget?: unknown }).DograhWidget;
    window.localStorage.clear();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it("uses Hong Kong Cantonese copy without exposing a cloud provider", () => {
    const { unmount } = render(<YishuiVoiceDemo embedToken="embed-test" />);

    expect(screen.getByText("AI 即時語音體驗")).not.toBeNull();
    expect(screen.getByText("準備好就可以開始")).not.toBeNull();
    expect(screen.getByText("玲玲師傅")).not.toBeNull();
    expect(document.querySelector("main")?.getAttribute("lang")).toBe("zh-HK");
    expect(document.body.textContent).not.toContain("阿里");
    expect(document.body.textContent).not.toContain("百煉");
    expect(document.body.textContent).not.toContain("易水 AI 顧問");
    expect(
      document.documentElement.classList.contains("yishui-voice-demo-page"),
    ).toBe(true);

    unmount();
    expect(
      document.documentElement.classList.contains("yishui-voice-demo-page"),
    ).toBe(false);
  });

  it("preconnects the voice pipeline before the consultation button is clicked", async () => {
    render(<YishuiVoiceDemo embedToken="embed-test" />);

    const startButton = await screen.findByRole("button", {
      name: "開始語音諮詢",
    });
    await waitFor(() => expect(widget.start).toHaveBeenCalledOnce());
    expect(screen.queryByText("開場字幕")).toBeNull();

    fireEvent.click(startButton);

    expect(widget.start).toHaveBeenCalledOnce();
    expect(await screen.findByText("開場字幕")).not.toBeNull();
  });

  it("keeps the existing circular recording button for the user-ready turn", async () => {
    render(<YishuiVoiceDemo embedToken="embed-test" />);

    const startButton = screen.getByRole("button", { name: "開始語音諮詢" });
    await waitFor(() => expect((startButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(startButton);

    expect(await screen.findByText("開場字幕")).not.toBeNull();
    expect(screen.getByText("你好，歡迎嚟到玲玲師傅。今日想問咩呢？")).not.toBeNull();

    act(() => agentReadyCallback(true));
    const recordButton = screen.getByRole("button", { name: "開始今輪錄音" });
    expect(recordButton.className).toContain("size-24");
    expect(recordButton.className).toContain("rounded-full");
  });

  it("hides the recording control while AI is processing", async () => {
    render(<YishuiVoiceDemo embedToken="embed-test" />);

    const startButton = screen.getByRole("button", { name: "開始語音諮詢" });
    await waitFor(() => expect((startButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(startButton);

    act(() => agentReadyCallback(true));
    const recordButton = screen.getByRole("button", { name: "開始今輪錄音" });
    fireEvent.click(recordButton);
    fireEvent.click(screen.getByRole("button", { name: "結束今輪錄音" }));

    expect(screen.getByText("玲玲師傅幫你合參緊")).not.toBeNull();
    expect(screen.getAllByText("辨識緊問事主題").length).toBeGreaterThan(0);
    expect(document.querySelector('[data-phase="processing"] button')).toBeNull();
    expect(document.body.textContent).not.toContain("正在组织粤语解读");
  });

  it("uses role-specific analysis stages with paced progress", () => {
    expect(PROCESSING_STEPS.map((step) => step.title)).toEqual([
      "辨識緊問事主題",
      "合參緊生肖同生辰線索",
      "查閱緊流年同傳統參考",
      "歸納緊趨吉避忌建議",
    ]);
    expect(PROCESSING_STEP_DELAYS_MS).toEqual([3_000, 7_000, 12_000]);
  });

  it("hides the recording control while the assistant is speaking Cantonese", async () => {
    render(<YishuiVoiceDemo embedToken="embed-test" />);

    const startButton = screen.getByRole("button", { name: "開始語音諮詢" });
    await waitFor(() => expect((startButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(startButton);

    act(() => agentReadyCallback(true));

    act(() => {
      voiceMessageCallback(
        {
          id: "voice-assistant-1",
          type: "assistant",
          role: "assistant",
          text: "我先同你睇下睡房嘅氣流同床位。",
          final: false,
          timestamp: new Date().toISOString(),
        },
        [],
      );
    });

    expect(screen.getByText("粵語回覆播放緊")).not.toBeNull();
    expect(document.querySelector('[data-phase="speaking"] button')).toBeNull();
  });

  it("preloads and immediately plays the configured client opening", async () => {
    const play = vi
      .spyOn(HTMLMediaElement.prototype, "play")
      .mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
    vi.spyOn(Math, "random").mockReturnValue(0.99);
    widget.getClientOpening.mockReturnValue({
      assetId: "greetings-04-test",
      audioUrl: "/voice-demo/warm-audio/greetings-04.wav",
      transcript: "哈囉，你嚟咗就已經令氣氛暖咗好多！",
      durationMs: 12080,
    });

    render(<YishuiVoiceDemo embedToken="embed-test" />);
    const startButton = screen.getByRole("button", { name: "開始語音諮詢" });
    await waitFor(() => expect((startButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(startButton);

    await waitFor(() => expect(play).toHaveBeenCalledOnce());
    const openingAudio = document.querySelector("audio");
    expect(openingAudio?.getAttribute("preload")).toBe("auto");
    expect(openingAudio?.getAttribute("src")).toBe(
      "/voice-demo/warm-audio/greetings-07.wav",
    );

    fireEvent.ended(openingAudio as HTMLAudioElement);
    fireEvent.error(openingAudio as HTMLAudioElement);
    expect(widget.completeClientOpening).toHaveBeenCalledOnce();
    expect(widget.completeClientOpening).toHaveBeenCalledWith(1);
  });

  it("completes the current opening attempt after a playback error without staying locked", async () => {
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
    widget.getClientOpening.mockReturnValue({
      assetId: "greetings-04-test",
      audioUrl: "/voice-demo/warm-audio/greetings-04.wav",
      transcript: "哈囉，你嚟咗就已經令氣氛暖咗好多！",
      durationMs: 12080,
    });

    render(<YishuiVoiceDemo embedToken="embed-test" />);
    const startButton = screen.getByRole("button", { name: "開始語音諮詢" });
    await waitFor(() => expect((startButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(startButton);

    const openingAudio = document.querySelector("audio") as HTMLAudioElement;
    await waitFor(() => expect(openingAudio.dataset.openingAttemptId).toBe("1"));
    fireEvent.error(openingAudio);

    expect(widget.completeClientOpening).toHaveBeenCalledOnce();
    expect(widget.completeClientOpening).toHaveBeenCalledWith(1);

    act(() => agentReadyCallback(true));
    expect(screen.getByRole("button", { name: "開始今輪錄音" })).not.toBeNull();

    fireEvent.ended(openingAudio);
    expect(widget.completeClientOpening).toHaveBeenCalledOnce();
  });
});
