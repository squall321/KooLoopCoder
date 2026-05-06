// Tree view contributing the LoopCoder sidebar: Sessions → Goals → Iterations.

import * as vscode from "vscode";
import { api, IterationView, SessionRef } from "./api";

export class SessionsProvider
    implements vscode.TreeDataProvider<TreeNode> {

    private _changes = new vscode.EventEmitter<TreeNode | undefined | void>();
    readonly onDidChangeTreeData = this._changes.event;

    refresh(): void {
        this._changes.fire();
    }

    getTreeItem(element: TreeNode): vscode.TreeItem {
        return element;
    }

    async getChildren(element?: TreeNode): Promise<TreeNode[]> {
        if (!element) {
            try {
                const sessions = await api.listSessions();
                return sessions.map(s => new SessionNode(s));
            } catch (e: any) {
                return [new ErrorNode(`API unreachable: ${e.message ?? e}`)];
            }
        }
        if (element instanceof SessionNode) {
            try {
                const detail = await api.getSession(element.session.id);
                return (detail.goals ?? []).map(g => new GoalNode(element.session.id, g));
            } catch (e: any) {
                return [new ErrorNode(`failed to load goals: ${e.message ?? e}`)];
            }
        }
        if (element instanceof GoalNode) {
            try {
                const iters = await api.listIterations(element.sessionId, element.goal.goal_id);
                return iters.map(i => new IterationNode(i));
            } catch (e: any) {
                return [new ErrorNode(`failed to load iterations: ${e.message ?? e}`)];
            }
        }
        return [];
    }
}

export abstract class TreeNode extends vscode.TreeItem { }

export class SessionNode extends TreeNode {
    constructor(public readonly session: SessionRef) {
        const dt = session.started_at
            ? new Date(session.started_at * 1000).toLocaleString()
            : "?";
        super(`${session.id} — ${session.status}`, vscode.TreeItemCollapsibleState.Collapsed);
        this.contextValue = "session";
        this.tooltip = `plan: ${session.plan_path ?? "(inline)"}\nstarted: ${dt}\ntokens: ${session.total_prompt_tokens}/${session.total_completion_tokens}`;
        this.iconPath = new vscode.ThemeIcon(this._statusIcon());
        this.description = session.plan_path
            ? session.plan_path.split("/").slice(-1)[0]
            : "(inline)";
    }
    private _statusIcon(): string {
        switch (this.session.status) {
            case "running": return "loading~spin";
            case "completed": return "check";
            case "stopped": return "circle-slash";
            case "error": return "error";
            default: return "circle-outline";
        }
    }
}

export class GoalNode extends TreeNode {
    constructor(public readonly sessionId: string, public readonly goal: any) {
        super(`${goal.goal_id} (${goal.iterations ?? 0} iter)`,
            vscode.TreeItemCollapsibleState.Collapsed);
        this.contextValue = "goal";
        this.iconPath = new vscode.ThemeIcon(
            goal.status === "passed" ? "pass-filled"
                : goal.status === "failed" ? "error"
                : goal.status === "stopped" ? "stop-circle"
                : "issue-opened"
        );
        this.description = goal.status;
    }
}

export class IterationNode extends TreeNode {
    constructor(public readonly it: IterationView) {
        super(`iter ${it.iter}`,
            vscode.TreeItemCollapsibleState.None);
        this.contextValue = "iteration";
        const verdict = it.verify_passed === null ? "?" : (it.verify_passed ? "PASS" : "FAIL");
        this.description = `${verdict} ${it.prompt_tokens}/${it.completion_tokens}t`;
        this.iconPath = new vscode.ThemeIcon(
            it.verify_passed === null ? "circle-outline"
                : it.verify_passed ? "check-all" : "x"
        );
        if (it.verify_log) {
            this.tooltip = it.verify_log.slice(0, 1500);
        }
    }
}

class ErrorNode extends TreeNode {
    constructor(public readonly message: string) {
        super(message, vscode.TreeItemCollapsibleState.None);
        this.iconPath = new vscode.ThemeIcon("warning");
        this.contextValue = "error";
    }
}
