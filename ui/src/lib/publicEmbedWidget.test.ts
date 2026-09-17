import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const widgetSource = readFileSync(
    resolve(process.cwd(), 'public/embed/dograh-widget.js'),
    'utf8',
);

type WidgetWindow = Window & {
    DograhWidget?: {
        init: () => Promise<void>;
        start: () => Promise<void>;
        stop: () => void;
        startChat: () => Promise<void>;
        endChat: () => Promise<unknown[] | null>;
        getState: () => {
            chat: { status: string };
            voice: { isRecording: boolean; agentReady: boolean };
        };
        getVoiceMessages: () => Array<{
            id: string;
            type: 'user' | 'assistant';
            role: 'user' | 'assistant';
            text: string;
            final: boolean;
            timestamp: string;
        }>;
        getOpeningTranscript: () => string | null;
        getClientOpening: () => {
            assetId: string | null;
            audioUrl: string;
            transcript: string;
            durationMs: number | null;
        } | null;
        getOpeningAttemptId: () => number;
        completeClientOpening: (attemptId: number) => boolean;
        onVoiceMessage: (callback: (message: {
            id: string;
            type: 'user' | 'assistant';
            role: 'user' | 'assistant';
            text: string;
            final: boolean;
            timestamp: string;
        }) => void) => void;
        onOpeningTranscript: (callback: (transcript: string) => void) => void;
        onAgentReady: (callback: (ready: boolean) => void) => void;
        onRecordingChange: (callback: (recording: boolean) => void) => void;
        startRecording: () => boolean;
        stopRecording: () => boolean;
        isRecording: () => boolean;
    };
};

async function flushMicrotasks() {
    for (let i = 0; i < 5; i += 1) {
        await Promise.resolve();
    }
}

function createFetchMock(autoStart: boolean) {
    return vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/api/v1/public/embed/config/')) {
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    workflow_id: 7,
                    texts: {
                        endChatText: 'End chat',
                        chatInputPlaceholder: 'Type a message',
                        sendMessageLabel: 'Send message',
                        conversationEndedText: 'Conversation ended.',
                        startNewChatText: 'Start a new chat',
                        chatRetryText: 'Retry',
                    },
                    settings: {
                        widgetType: 'chat',
                        embedMode: 'inline',
                        containerId: 'dograh-inline-container',
                    },
                    auto_start: autoStart,
                }),
            } as Response;
        }

        if (url.includes('/api/v1/public/embed/chat/') && url.endsWith('/end')) {
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    revision: 3,
                    state: 'completed',
                    is_completed: true,
                    turns: [],
                }),
            } as Response;
        }

        return {
            ok: true,
            status: 200,
            json: async () => ({
                session_token: 'emb_session_TEST',
                workflow_run_id: 101,
                chat_session: {
                    revision: 2,
                    state: 'running',
                    is_completed: false,
                    turns: [],
                },
            }),
        } as Response;
    });
}

function countInitCalls(fetchMock: ReturnType<typeof createFetchMock>) {
    return fetchMock.mock.calls.filter(([url]) =>
        String(url).endsWith('/api/v1/public/embed/init'),
    ).length;
}

async function loadWidget(fetchMock: ReturnType<typeof createFetchMock>) {
    vi.stubGlobal('fetch', fetchMock);
    window.eval(widgetSource);
    await flushMicrotasks();

    const widget = (window as WidgetWindow).DograhWidget;
    expect(widget).toBeDefined();
    if (fetchMock.mock.calls.length === 0) {
        await widget?.init();
    }
    await flushMicrotasks();
    return widget as NonNullable<WidgetWindow['DograhWidget']>;
}

function createVoiceFetchMock() {
    return vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/api/v1/public/embed/config/')) {
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    workflow_id: 7,
                    texts: {
                        voiceConnectingText: 'Connecting',
                        voiceConnectingSubtext: 'Preparing the call',
                        voiceConnectedTitle: 'Connected',
                        voiceConnectedSubtext: 'The agent is ready',
                        voiceCallEndedTitle: 'Call ended',
                        voiceCallEndedSubtext: 'The call has ended',
                    },
                    settings: {
                        widgetType: 'voice',
                        embedMode: 'headless',
                        fastOpening: true,
                        clientOpeningAssetId: 'greetings-04-test',
                        clientOpeningAudioUrl: '/voice-demo/warm-audio/greetings-04.wav',
                        clientOpeningTranscript: '你好，我係易水 AI 顧問。',
                        clientOpeningDurationMs: 12080,
                        recordingDrainMs: 1200,
                    },
                    auto_start: false,
                    turn_enabled: false,
                }),
            } as Response;
        }

        if (url.endsWith('/api/v1/public/embed/init')) {
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    session_token: 'emb_session_VOICE',
                    workflow_run_id: 202,
                    config: { workflow_id: 7 },
                    opening_transcript: '你好，我係易水 AI 顧問。',
                }),
            } as Response;
        }

        throw new Error(`Unexpected request: ${url}`);
    });
}

describe('public embed widget chat lifecycle', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        document.head.innerHTML = '';
        document.body.innerHTML = `
            <script src="http://widget.test/embed/dograh-widget.js?token=emb_TEST"></script>
            <div id="dograh-inline-container"></div>
        `;
    });

    afterEach(() => {
        delete (window as WidgetWindow).DograhWidget;
        vi.useRealTimers();
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
        document.head.innerHTML = '';
        document.body.innerHTML = '';
    });

    it('auto-start replaces the inline CTA with the started conversation', async () => {
        const fetchMock = createFetchMock(true);
        await loadWidget(fetchMock);
        await vi.advanceTimersByTimeAsync(1000);
        await flushMicrotasks();

        expect(countInitCalls(fetchMock)).toBe(1);
        expect(document.querySelector('.dograh-chat-inline-cta')).toBeNull();
        expect(document.querySelector('.dograh-chat-panel--inline')).not.toBeNull();
    });

    it('public startChat opens the inline panel and reuses its session', async () => {
        const fetchMock = createFetchMock(false);
        const widget = await loadWidget(fetchMock);

        expect(document.querySelector('.dograh-chat-inline-cta')).not.toBeNull();
        expect(countInitCalls(fetchMock)).toBe(0);

        await widget.startChat();
        await flushMicrotasks();

        expect(countInitCalls(fetchMock)).toBe(1);
        expect(document.querySelector('.dograh-chat-inline-cta')).toBeNull();
        expect(document.querySelector('.dograh-chat-panel--inline')).not.toBeNull();

        await widget.startChat();
        await flushMicrotasks();
        expect(countInitCalls(fetchMock)).toBe(1);
    });

    it('shows an end-chat action that completes the server session', async () => {
        const fetchMock = createFetchMock(false);
        const widget = await loadWidget(fetchMock);

        await widget.startChat();
        await flushMicrotasks();

        const endButton = document.querySelector<HTMLButtonElement>('.dograh-chat-end');
        expect(endButton).not.toBeNull();
        expect(endButton?.disabled).toBe(false);

        endButton?.click();
        await flushMicrotasks();

        expect(fetchMock.mock.calls.some(([url]) =>
            String(url).endsWith('/api/v1/public/embed/chat/emb_session_TEST/end'),
        )).toBe(false);

        const confirmEndButton = document.querySelector<HTMLButtonElement>(
            '.dograh-chat-end-confirm-submit',
        );
        expect(confirmEndButton).not.toBeNull();
        confirmEndButton?.click();
        await flushMicrotasks();

        const endCalls = fetchMock.mock.calls.filter(([url]) =>
            String(url).endsWith('/api/v1/public/embed/chat/emb_session_TEST/end'),
        );
        expect(endCalls).toHaveLength(1);
        expect(widget.getState().chat.status).toBe('ended');
        expect(document.querySelector('.dograh-chat-banner')?.textContent).toContain('Conversation ended.');
        expect(document.querySelector<HTMLButtonElement>('.dograh-chat-send')?.disabled).toBe(true);
    });

    it('generic start waits for chat configuration before choosing a flow', async () => {
        let resolveConfig: (response: Response) => void = () => undefined;
        const configResponse = new Promise<Response>((resolve) => {
            resolveConfig = resolve;
        });
        const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
            const url = String(input);
            if (url.includes('/api/v1/public/embed/config/')) {
                return configResponse;
            }
            if (url.endsWith('/api/v1/public/embed/init')) {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        session_token: 'emb_session_TEST',
                        workflow_run_id: 101,
                        config: { workflow_id: 7 },
                        chat_session: {
                            revision: 2,
                            state: 'running',
                            is_completed: false,
                            turns: [],
                        },
                    }),
                } as Response;
            }
            if (url.includes('/turn-credentials/')) {
                return { ok: false, status: 503 } as Response;
            }
            throw new Error(`Unexpected request: ${url}`);
        });
        const getUserMedia = vi.fn().mockRejectedValue(
            Object.assign(new Error('permission denied'), { name: 'NotAllowedError' }),
        );
        vi.stubGlobal('fetch', fetchMock);
        vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } });

        window.eval(widgetSource);
        await flushMicrotasks();
        const widget = (window as WidgetWindow).DograhWidget;
        expect(widget).toBeDefined();

        const startPromise = widget?.start();
        await flushMicrotasks();

        expect(countInitCalls(fetchMock)).toBe(0);
        expect(getUserMedia).not.toHaveBeenCalled();

        resolveConfig({
            ok: true,
            status: 200,
            json: async () => ({
                workflow_id: 7,
                settings: {
                    widgetType: 'chat',
                    embedMode: 'inline',
                    containerId: 'dograh-inline-container',
                },
                auto_start: false,
            }),
        } as Response);
        await startPromise;
        await flushMicrotasks();

        const configCalls = fetchMock.mock.calls.filter(([url]) =>
            String(url).includes('/api/v1/public/embed/config/'),
        );
        expect(configCalls).toHaveLength(1);
        expect(countInitCalls(fetchMock)).toBe(1);
        expect(getUserMedia).not.toHaveBeenCalled();
        expect(document.querySelector('.dograh-chat-panel--inline')).not.toBeNull();
    });
});

describe('public embed widget headless voice lifecycle', () => {
    beforeEach(() => {
        document.head.innerHTML = '';
        document.body.innerHTML = `
            <script src="http://widget.test/embed/dograh-widget.js?token=emb_TEST"></script>
        `;
    });

    afterEach(() => {
        delete (window as WidgetWindow).DograhWidget;
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
        document.head.innerHTML = '';
        document.body.innerHTML = '';
    });

    it('keeps the mic muted, streams transcript updates, and supports push-to-talk turns', async () => {
        const microphoneTrack = {
            kind: 'audio',
            enabled: true,
            stop: vi.fn(),
        };
        const microphoneStream = {
            getTracks: () => [microphoneTrack],
            getAudioTracks: () => [microphoneTrack],
        };
        const getUserMedia = vi.fn().mockResolvedValue(microphoneStream);

        const peerConnections: FakePeerConnection[] = [];
        class FakePeerConnection {
            connectionState = 'new';
            iceConnectionState = 'new';
            signalingState = 'stable';
            ontrack: ((event: { track: { kind: string }; streams: unknown[] }) => void) | null = null;
            oniceconnectionstatechange: (() => void) | null = null;
            onconnectionstatechange: (() => void) | null = null;
            onicecandidate: ((event: { candidate: null }) => void) | null = null;
            addTrack = vi.fn();
            close = vi.fn(() => {
                this.signalingState = 'closed';
            });
            setRemoteDescription = vi.fn().mockResolvedValue(undefined);
            addIceCandidate = vi.fn().mockResolvedValue(undefined);
            setLocalDescription = vi.fn().mockResolvedValue(undefined);
            createOffer = vi.fn().mockResolvedValue({ type: 'offer', sdp: 'fake-offer' });

            constructor() {
                peerConnections.push(this);
            }
        }

        const sockets: FakeWebSocket[] = [];
        class FakeWebSocket {
            static OPEN = 1;
            static CLOSING = 2;
            static CLOSED = 3;
            readyState = FakeWebSocket.OPEN;
            onopen: (() => void) | null = null;
            onerror: ((error: unknown) => void) | null = null;
            onclose: ((event: { reason: string }) => void) | null = null;
            onmessage: ((event: { data: string }) => Promise<void> | void) | null = null;
            send = vi.fn();

            constructor() {
                sockets.push(this);
                queueMicrotask(() => this.onopen?.());
            }

            close() {
                this.readyState = FakeWebSocket.CLOSED;
            }

            async emit(type: string, payload: Record<string, unknown> = {}) {
                await this.onmessage?.({ data: JSON.stringify({ type, payload }) });
            }
        }

        const speechRecognizers: FakeSpeechRecognition[] = [];
        class FakeSpeechRecognition {
            lang = '';
            continuous = false;
            interimResults = false;
            onresult: ((event: {
                resultIndex: number;
                results: Array<{ 0: { transcript: string }; isFinal: boolean }>;
            }) => void) | null = null;
            onerror: (() => void) | null = null;
            onend: (() => void) | null = null;
            start = vi.fn();
            stop = vi.fn();
            abort = vi.fn();

            constructor() {
                speechRecognizers.push(this);
            }

            emit(text: string, isFinal = false) {
                this.onresult?.({
                    resultIndex: 0,
                    results: [{ 0: { transcript: text }, isFinal }],
                });
            }
        }

        const fetchMock = createVoiceFetchMock();
        vi.stubGlobal('fetch', fetchMock);
        vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } });
        vi.stubGlobal('RTCPeerConnection', FakePeerConnection);
        vi.stubGlobal('WebSocket', FakeWebSocket);
        vi.stubGlobal('webkitSpeechRecognition', FakeSpeechRecognition);

        window.eval(widgetSource);
        await flushMicrotasks();
        const widget = (window as WidgetWindow).DograhWidget;
        expect(widget).toBeDefined();
        if (fetchMock.mock.calls.length === 0) {
            await widget?.init();
        }
        await flushMicrotasks();

        const observedTexts: string[] = [];
        const readiness: boolean[] = [];
        const recordingChanges: boolean[] = [];
        const openingTranscripts: string[] = [];
        widget?.onVoiceMessage(message => observedTexts.push(message.text));
        widget?.onOpeningTranscript(transcript => openingTranscripts.push(transcript));
        widget?.onAgentReady(ready => readiness.push(ready));
        widget?.onRecordingChange(recording => recordingChanges.push(recording));

        await widget?.start();
        await flushMicrotasks();

        expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
        expect(widget?.getOpeningTranscript()).toBe('你好，我係易水 AI 顧問。');
        expect(openingTranscripts).toEqual(['你好，我係易水 AI 顧問。']);
        expect(widget?.getClientOpening()).toMatchObject({
            assetId: 'greetings-04-test',
            audioUrl: '/voice-demo/warm-audio/greetings-04.wav',
            transcript: '你好，我係易水 AI 顧問。',
            durationMs: 12080,
        });
        expect(microphoneTrack.enabled).toBe(false);
        expect(widget?.startRecording()).toBe(false);

        const socket = sockets[0];
        // The local opening can finish while the provider warms in parallel,
        // but the first recording waits until server ASR is actually ready.
        expect(widget?.completeClientOpening(widget.getOpeningAttemptId())).toBe(true);
        expect(readiness).toEqual([]);

        const peerConnection = peerConnections[0];
        peerConnection.connectionState = 'connected';
        peerConnection.onconnectionstatechange?.();
        expect(readiness).toEqual([]);
        expect(widget?.startRecording()).toBe(false);

        await socket.emit('rtf-transcriber-ready', { ready: true });
        expect(readiness).toEqual([true]);
        expect(widget?.getVoiceMessages()).toEqual([]);
        expect(widget?.startRecording()).toBe(true);
        expect(widget?.isRecording()).toBe(true);
        expect(microphoneTrack.enabled).toBe(true);
        expect(widget?.getVoiceMessages()).toMatchObject([
            { type: 'user', text: '正在聆听…', final: false },
        ]);
        expect(speechRecognizers).toHaveLength(1);
        expect(speechRecognizers[0].lang).toBe('zh-HK');
        speechRecognizers[0].emit('我想睇今年運程');
        expect(widget?.getVoiceMessages()).toMatchObject([
            { type: 'user', text: '我想睇今年運程', final: false },
        ]);

        await socket.emit('rtf-user-transcription', { text: '卧', final: false });
        await socket.emit('rtf-user-transcription', { text: '卧室', final: false });
        expect(widget?.stopRecording()).toBe(true);
        expect(widget?.isRecording()).toBe(false);
        // The sender remains live briefly so Fun-ASR can observe its 800ms
        // trailing-silence boundary and emit a final-only transcription.
        expect(microphoneTrack.enabled).toBe(true);

        // The agent taking its turn automatically closes push-to-talk while
        // preserving the connected WebRTC session and prior transcript.
        await socket.emit('rtf-bot-started-speaking');
        expect(widget?.isRecording()).toBe(false);
        expect(microphoneTrack.enabled).toBe(false);
        expect(recordingChanges).toEqual([true, false]);

        await socket.emit('rtf-bot-text', { text: '床头宜有靠。' });
        await socket.emit('rtf-user-transcription', { text: '卧室应该怎样布局？', final: true });
        expect(microphoneTrack.enabled).toBe(false);
        expect(widget?.getVoiceMessages()).toMatchObject([
            { type: 'user', role: 'user', text: '卧室应该怎样布局？', final: true },
            { type: 'assistant', text: '床头宜有靠。', final: false },
        ]);

        await socket.emit('rtf-bot-stopped-speaking');
        expect(widget?.getVoiceMessages()).toMatchObject([
            { type: 'user', text: '卧室应该怎样布局？', final: true },
            { type: 'assistant', text: '床头宜有靠。', final: true },
        ]);
        expect(observedTexts).toContain('卧室应该怎样布局？');

        widget?.stop();
        expect(microphoneTrack.stop).toHaveBeenCalledOnce();
        expect(widget?.getVoiceMessages()).toEqual([]);
        expect(widget?.getState().voice).toMatchObject({
            isRecording: false,
            agentReady: false,
        });
        expect(readiness).toEqual([true, false, true, false]);
    });
});
