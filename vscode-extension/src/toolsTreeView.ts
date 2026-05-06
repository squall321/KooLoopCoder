// Read-only Tools view: lists every tool the LoopCoder agent exposes.

import * as vscode from "vscode";
import { api, ToolMeta } from "./api";

export class ToolsProvider implements vscode.TreeDataProvider<ToolNode> {
    private _changes = new vscode.EventEmitter<void>();
    readonly onDidChangeTreeData = this._changes.event;

    refresh(): void { this._changes.fire(); }

    getTreeItem(el: ToolNode): vscode.TreeItem { return el; }

    async getChildren(): Promise<ToolNode[]> {
        try {
            const tools = await api.listTools();
            return tools.map(t => new ToolNode(t));
        } catch {
            return [];
        }
    }
}

class ToolNode extends vscode.TreeItem {
    constructor(public readonly meta: ToolMeta) {
        super(meta.name, vscode.TreeItemCollapsibleState.None);
        this.tooltip = meta.description;
        this.description = (meta.description.split(".")[0] ?? "").slice(0, 80);
        this.iconPath = new vscode.ThemeIcon("tools");
    }
}
