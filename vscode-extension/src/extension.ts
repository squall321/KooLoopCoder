// LoopCoder VS Code extension entry point.

import * as vscode from "vscode";
import { api, streamSessionEvents } from "./api";
import { SessionsProvider } from "./sessionsTreeView";
import { ToolsProvider } from "./toolsTreeView";

let output: vscode.OutputChannel;
let statusBar: vscode.StatusBarItem;

export async function activate(ctx: vscode.ExtensionContext): Promise<void> {
    output = vscode.window.createOutputChannel("LoopCoder");
    statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBar.text = "$(loading~spin) LoopCoder";
    statusBar.tooltip = "LoopCoder agent";
    statusBar.command = "loopcoder.showHealth";
    statusBar.show();

    // ---- views ----
    const sessions = new SessionsProvider();
    const tools = new ToolsProvider();
    ctx.subscriptions.push(
        vscode.window.registerTreeDataProvider("loopcoder.sessions", sessions),
        vscode.window.registerTreeDataProvider("loopcoder.tools", tools)
    );

    // Periodic refresh while extension is open
    const tick = setInterval(() => sessions.refresh(), 3000);
    ctx.subscriptions.push({ dispose: () => clearInterval(tick) });

    // ---- commands ----
    ctx.subscriptions.push(
        vscode.commands.registerCommand("loopcoder.refreshSessions", () => {
            sessions.refresh();
            tools.refresh();
        }),
        vscode.commands.registerCommand("loopcoder.showHealth", showHealth),
        vscode.commands.registerCommand("loopcoder.runActivePlan", () => runActivePlan(sessions)),
        vscode.commands.registerCommand("loopcoder.runPlan", () => runPlanPicker(sessions)),
        vscode.commands.registerCommand("loopcoder.stopSession", stopSession),
        vscode.commands.registerCommand("loopcoder.openSessionReport", openSessionReport),
        vscode.commands.registerCommand("loopcoder.exportSession", exportSession)
    );

    // ---- initial health probe ----
    pingHealth();
}

export function deactivate(): void { /* nothing */ }

// ----------- commands -----------

async function showHealth(): Promise<void> {
    try {
        const h = await api.health();
        vscode.window.showInformationMessage(
            `LoopCoder API ok — v${h.version}, ${h.sessions_active} active session(s)`
        );
    } catch (e: any) {
        vscode.window.showErrorMessage(`LoopCoder API unreachable: ${e.message ?? e}`);
    }
}

async function pingHealth(): Promise<void> {
    try {
        const h = await api.health();
        statusBar.text = `$(rocket) LoopCoder ${h.version}`;
        statusBar.tooltip = `${h.sessions_active} active session(s)`;
    } catch {
        statusBar.text = "$(warning) LoopCoder offline";
        statusBar.tooltip = "Run: loopcoder serve";
    }
}

async function runActivePlan(sessions: SessionsProvider): Promise<void> {
    const ed = vscode.window.activeTextEditor;
    if (!ed) {
        vscode.window.showWarningMessage("No active editor.");
        return;
    }
    const path = ed.document.uri.fsPath;
    if (!path.endsWith(".yaml")) {
        vscode.window.showWarningMessage("Active file must be a plan.yaml.");
        return;
    }
    await runFromPath(path, sessions);
}

async function runPlanPicker(sessions: SessionsProvider): Promise<void> {
    const uri = await vscode.window.showOpenDialog({
        canSelectFiles: true, canSelectFolders: false, canSelectMany: false,
        openLabel: "Run this plan",
        filters: { "Plan YAML": ["yaml", "yml"] }
    });
    if (!uri || uri.length === 0) {
        return;
    }
    await runFromPath(uri[0].fsPath, sessions);
}

async function runFromPath(planPath: string, sessions: SessionsProvider): Promise<void> {
    output.clear();
    output.show(true);
    output.appendLine(`▶ starting session from ${planPath}`);
    let ref;
    try {
        ref = await api.startFromPath(planPath);
    } catch (e: any) {
        output.appendLine(`error: ${e.message ?? e}`);
        vscode.window.showErrorMessage(`LoopCoder: failed to start session: ${e.message ?? e}`);
        return;
    }
    output.appendLine(`session ${ref.id} created (${ref.status})`);
    sessions.refresh();
    streamLog(ref.id);
}

async function streamLog(sid: string): Promise<void> {
    const ac = new AbortController();
    try {
        for await (const ev of streamSessionEvents(sid, ac.signal)) {
            const t = ev.event;
            const d = ev.data;
            if (t === "heartbeat") { continue; }
            const goal = d.goal_id ? ` ${d.goal_id}` : "";
            const it = d.iter ? ` #${d.iter}` : "";
            output.appendLine(`  [${t}]${goal}${it}  ${summarize(d.payload)}`);
            if (t === "session.ended") {
                output.appendLine(`✓ session ended (${d.payload?.status})`);
                break;
            }
        }
    } catch (e: any) {
        output.appendLine(`stream error: ${e.message ?? e}`);
    }
}

function summarize(payload: any): string {
    if (!payload) { return ""; }
    const keys = Object.keys(payload);
    return keys.slice(0, 3).map(k => `${k}=${shorten(payload[k])}`).join(" ");
}

function shorten(v: any): string {
    if (v === null || v === undefined) { return "-"; }
    const s = typeof v === "string" ? v : JSON.stringify(v);
    return s.length > 80 ? s.slice(0, 77) + "..." : s;
}

async function stopSession(): Promise<void> {
    try {
        const list = await api.listSessions();
        const running = list.filter(s => s.status === "running");
        if (running.length === 0) {
            vscode.window.showInformationMessage("No running sessions.");
            return;
        }
        const pick = await vscode.window.showQuickPick(
            running.map(s => ({ label: s.id, detail: s.plan_path ?? "(inline)" })),
            { placeHolder: "Stop which session?" }
        );
        if (!pick) { return; }
        await api.stop(pick.label, "user requested via VS Code");
        vscode.window.showInformationMessage(`Stop requested for ${pick.label}.`);
    } catch (e: any) {
        vscode.window.showErrorMessage(`Failed to stop: ${e.message ?? e}`);
    }
}

async function openSessionReport(node?: any): Promise<void> {
    const sid = node?.session?.id ?? await pickSessionId();
    if (!sid) { return; }
    try {
        const md = await api.report(sid);
        const doc = await vscode.workspace.openTextDocument({ content: md, language: "markdown" });
        await vscode.window.showTextDocument(doc, { preview: false });
    } catch (e: any) {
        vscode.window.showErrorMessage(`Report failed: ${e.message ?? e}`);
    }
}

async function exportSession(node?: any): Promise<void> {
    const sid = node?.session?.id ?? await pickSessionId();
    if (!sid) { return; }
    const url = api.exportUrl(sid);
    vscode.env.openExternal(vscode.Uri.parse(url));
}

async function pickSessionId(): Promise<string | undefined> {
    const list = await api.listSessions();
    if (list.length === 0) {
        vscode.window.showInformationMessage("No sessions yet.");
        return undefined;
    }
    const pick = await vscode.window.showQuickPick(
        list.map(s => ({ label: s.id, detail: s.plan_path ?? "(inline)" })),
        { placeHolder: "Pick a session" }
    );
    return pick?.label;
}
