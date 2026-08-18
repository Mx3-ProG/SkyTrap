import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, apiRequest } from "../api/client";

type ProgressEvent = {
  type: "turn_progress";
  turn_id: string;
  tool: string;
  arguments: Record<string, unknown>;
  observation: string;
};

type ConfirmRequest = {
  type: "confirm_request";
  id: string;
  kind: string;
  preview: string;
};

type TurnComplete = {
  type: "turn_complete";
  turn_id: string;
  status: "done" | "error";
  result: string | null;
  error: string | null;
};

type ServerMessage = ProgressEvent | ConfirmRequest | TurnComplete;

export type ConnectionState = "connecting" | "open" | "closed";

function wsUrl(): string {
  const base = API_BASE || window.location.origin;
  return base.replace(/^http/, "ws") + "/ws";
}

/** Owns the single active WebSocket connection to SkyTrap — mirrors the
 * server's ConnectionManager (one connection at a time, matching the
 * single-user design). Reconnects once after a 4401 close (expired access
 * token) by refreshing the session first; any other close is surfaced as
 * "closed" rather than retried silently. */
export function useTurnSocket() {
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [progress, setProgress] = useState<ProgressEvent[]>([]);
  const [pendingConfirm, setPendingConfirm] = useState<ConfirmRequest | null>(null);
  const [result, setResult] = useState<TurnComplete | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const hasRetriedAuth = useRef(false);

  const connect = useCallback(() => {
    setConnectionState("connecting");
    const socket = new WebSocket(wsUrl());
    socketRef.current = socket;

    socket.onopen = () => {
      hasRetriedAuth.current = false;
      setConnectionState("open");
    };

    socket.onmessage = (event) => {
      const message: ServerMessage = JSON.parse(event.data);
      if (message.type === "turn_progress") {
        setProgress((prev) => [...prev, message]);
      } else if (message.type === "confirm_request") {
        setPendingConfirm(message);
      } else if (message.type === "turn_complete") {
        setResult(message);
        setPendingConfirm(null);
      }
    };

    socket.onclose = (event) => {
      setConnectionState("closed");
      if (event.code === 4401 && !hasRetriedAuth.current) {
        hasRetriedAuth.current = true;
        apiRequest("/auth/refresh", { method: "POST" }).then((response) => {
          if (response.ok) connect();
        });
      }
    };
  }, []);

  useEffect(() => {
    connect();
    return () => socketRef.current?.close();
  }, [connect]);

  const startTurn = useCallback(async (task: string, workspace: string) => {
    setProgress([]);
    setResult(null);
    const response = await apiRequest("/turns", {
      method: "POST",
      body: JSON.stringify({ task, workspace }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `Failed to start turn: ${response.status}`);
    }
    return response.json() as Promise<{ turn_id: string }>;
  }, []);

  const respondToConfirm = useCallback(
    (answer: boolean) => {
      if (!pendingConfirm || socketRef.current?.readyState !== WebSocket.OPEN) return;
      socketRef.current.send(
        JSON.stringify({ type: "confirm_response", id: pendingConfirm.id, answer })
      );
      setPendingConfirm(null);
    },
    [pendingConfirm]
  );

  return { connectionState, progress, pendingConfirm, result, startTurn, respondToConfirm };
}
