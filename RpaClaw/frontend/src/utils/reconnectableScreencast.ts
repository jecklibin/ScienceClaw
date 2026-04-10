export interface ScreencastCloseInfo {
  code: number;
  reason: string;
  wasClean: boolean;
}

export interface ScreencastStatusEvent {
  phase: 'connecting' | 'open' | 'reconnecting' | 'closed' | 'failed';
  attempt?: number;
  delayMs?: number;
  close?: ScreencastCloseInfo;
}

interface ReconnectableScreencastOptions {
  debugLabel: string;
  getUrl: () => string;
  maxReconnectAttempts?: number;
  initialReconnectDelayMs?: number;
  maxReconnectDelayMs?: number;
  onMessage: (event: MessageEvent<string>) => void;
  onStatusChange?: (event: ScreencastStatusEvent) => void;
  onError?: (event: Event) => void;
}

export interface ReconnectableScreencast {
  connect: () => void;
  stop: () => void;
  send: (payload: string) => boolean;
  isOpen: () => boolean;
}

export function createReconnectableScreencast(
  options: ReconnectableScreencastOptions,
): ReconnectableScreencast {
  const maxReconnectAttempts = options.maxReconnectAttempts ?? 8;
  const initialReconnectDelayMs = options.initialReconnectDelayMs ?? 500;
  const maxReconnectDelayMs = options.maxReconnectDelayMs ?? 5000;

  let ws: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let reconnectAttempts = 0;
  let manuallyStopped = false;

  const clearReconnectTimer = () => {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  const clearSocket = () => {
    if (!ws) return;
    ws.onopen = null;
    ws.onclose = null;
    ws.onerror = null;
    ws.onmessage = null;
    ws = null;
  };

  const scheduleReconnect = (close: ScreencastCloseInfo) => {
    if (manuallyStopped) return;
    if (reconnectAttempts >= maxReconnectAttempts) {
      options.onStatusChange?.({ phase: 'failed', attempt: reconnectAttempts, close });
      return;
    }

    reconnectAttempts += 1;
    const delayMs = Math.min(
      initialReconnectDelayMs * Math.pow(2, Math.max(0, reconnectAttempts - 1)),
      maxReconnectDelayMs,
    );
    options.onStatusChange?.({
      phase: 'reconnecting',
      attempt: reconnectAttempts,
      delayMs,
      close,
    });

    clearReconnectTimer();
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, delayMs);
  };

  const connect = () => {
    manuallyStopped = false;
    clearReconnectTimer();

    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    if (ws) {
      ws.close();
      clearSocket();
    }

    const url = options.getUrl();
    options.onStatusChange?.({ phase: 'connecting' });
    console.log(`[${options.debugLabel}] Connecting screencast:`, url);

    const nextSocket = new WebSocket(url);
    ws = nextSocket;

    nextSocket.onopen = () => {
      if (ws !== nextSocket) return;
      reconnectAttempts = 0;
      console.log(`[${options.debugLabel}] Screencast connected`);
      options.onStatusChange?.({ phase: 'open' });
    };

    nextSocket.onmessage = (event) => {
      if (ws !== nextSocket) return;
      options.onMessage(event);
    };

    nextSocket.onerror = (event) => {
      if (ws !== nextSocket) return;
      console.error(`[${options.debugLabel}] Screencast error:`, event);
      options.onError?.(event);
    };

    nextSocket.onclose = (event) => {
      if (ws !== nextSocket) return;
      console.warn(`[${options.debugLabel}] Screencast closed:`, event.code, event.reason);
      clearSocket();

      const close = {
        code: event.code,
        reason: event.reason,
        wasClean: event.wasClean,
      };
      if (manuallyStopped) {
        options.onStatusChange?.({ phase: 'closed', close });
        return;
      }
      scheduleReconnect(close);
    };
  };

  const stop = () => {
    manuallyStopped = true;
    clearReconnectTimer();
    if (!ws) return;
    const activeSocket = ws;
    clearSocket();
    activeSocket.close();
  };

  const send = (payload: string) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(payload);
    return true;
  };

  const isOpen = () => ws?.readyState === WebSocket.OPEN;

  return {
    connect,
    stop,
    send,
    isOpen,
  };
}
