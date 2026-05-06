// Thin client around the LoopCoder HTTP API.
// Uses Node's fetch (Node 18+ which VS Code ships with).

import * as vscode from "vscode";

export interface SessionRef {
    id: string;
    status: string;
    plan_path: string | null;
    started_at: number | null;
    ended_at: number | null;
    total_prompt_tokens: number;
    total_completion_tokens: number;
}

export interface ToolMeta {
    name: string;
    description: string;
    parameters: any;
}

export interface IterationView {
    iter: number;
    prompt_tokens: number;
    completion_tokens: number;
    verify_passed: boolean | null;
    verify_log: string | null;
}

function cfg(): { apiUrl: string; apiKey: string } {
    const c = vscode.workspace.getConfiguration("loopcoder");
    return {
        apiUrl: (c.get<string>("apiUrl") || "http://127.0.0.1:8765").replace(/\/$/, ""),
        apiKey: c.get<string>("apiKey") || ""
    };
}

function authHeaders(): Record<string, string> {
    const { apiKey } = cfg();
    if (!apiKey) {
        return { "content-type": "application/json" };
    }
    return {
        "content-type": "application/json",
        "authorization": `Bearer ${apiKey}`
    };
}

async function call<T>(method: string, path: string, body?: any): Promise<T> {
    const { apiUrl } = cfg();
    const res = await fetch(`${apiUrl}${path}`, {
        method,
        headers: authHeaders(),
        body: body !== undefined ? JSON.stringify(body) : undefined
    });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(`${method} ${path} -> ${res.status}: ${text}`);
    }
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
        return (await res.json()) as T;
    }
    return (await res.text()) as unknown as T;
}

export const api = {
    health: () => call<{ status: string; version: string; sessions_active: number }>("GET", "/v1/health"),
    listTools: () => call<ToolMeta[]>("GET", "/v1/tools"),
    listSessions: () => call<SessionRef[]>("GET", "/v1/sessions"),
    getSession: (sid: string) => call<{ session: any; goals: any[] }>("GET", `/v1/sessions/${sid}`),
    listIterations: (sid: string, gid: string) =>
        call<IterationView[]>("GET", `/v1/sessions/${sid}/iterations/${gid}`),
    startFromPath: (planPath: string, onlyGoal?: string) =>
        call<SessionRef>("POST", "/v1/sessions:from-path", { plan_path: planPath, only_goal: onlyGoal }),
    startInline: (plan: any, onlyGoal?: string) =>
        call<SessionRef>("POST", "/v1/sessions", { plan, only_goal: onlyGoal }),
    stop: (sid: string, reason?: string) =>
        call<{ requested: boolean; session_id: string }>("POST", `/v1/sessions/${sid}:stop`, { reason }),
    report: (sid: string) => call<string>("GET", `/v1/sessions/${sid}/report`),
    exportUrl: (sid: string) => `${cfg().apiUrl}/v1/sessions/${sid}/export.tar.gz`,
    eventsUrl: (sid: string) => `${cfg().apiUrl}/v1/sessions/${sid}/events`
};

// SSE consumer for the live event stream (used by the Output channel).
// VS Code does not ship EventSource, so we read the body as a stream.
export async function* streamSessionEvents(sid: string, signal?: AbortSignal):
    AsyncGenerator<{ event: string; data: any }> {
    const { apiUrl } = cfg();
    const res = await fetch(`${apiUrl}/v1/sessions/${sid}/events`, {
        method: "GET",
        headers: { ...authHeaders(), "accept": "text/event-stream" },
        signal
    });
    if (!res.ok || !res.body) {
        throw new Error(`events stream failed: ${res.status}`);
    }
    const reader = (res.body as any).getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
        const { value, done } = await reader.read();
        if (done) {
            break;
        }
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        let evType = "message";
        let dataStr = "";
        for (const ln of lines) {
            if (ln.startsWith("event:")) {
                evType = ln.slice(6).trim();
            } else if (ln.startsWith("data:")) {
                dataStr += ln.slice(5).trim();
            } else if (ln === "" && dataStr) {
                let parsed: any = dataStr;
                try { parsed = JSON.parse(dataStr); } catch { /* leave as text */ }
                yield { event: evType, data: parsed };
                evType = "message";
                dataStr = "";
            }
        }
    }
}
