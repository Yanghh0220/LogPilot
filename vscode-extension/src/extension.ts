// extension.ts - LogGazer VS Code Extension entry point
//
// Architecture:
//   VS Code Terminal/Editor → analyzeTerminalSelection command
//     → LogGazerClient.analyze() → FastAPI /v1/analyze
//     → AnalysisPanel (Webview) renders structured result
//
// Backend lifecycle:
//   - On activation, silently checks backend health
//   - Status bar indicator shows connectivity (green=connected, yellow=checking, red=offline)
//   - "Start Backend" command launches `python -m api.main` in a VS Code terminal
//   - When analysis is requested and backend is down, offers to start it
//   - If loggazer.autoStartBackend is true (default), auto-starts on activation
//
// Activation: onStartupFinished (so we can check health without slowing editor startup)

import * as vscode from 'vscode';
import { analyzeTerminalSelection } from './commands/analyzeSelection';
import { AnalysisPanel } from './panels/AnalysisPanel';
import { LogGazerClient } from './api/loggazerClient';

let statusBarItem: vscode.StatusBarItem;
let healthCheckTimer: NodeJS.Timeout | undefined;

export function activate(context: vscode.ExtensionContext) {
    console.log('LogGazer extension activated');

    // ---- Status Bar Item ----
    statusBarItem = vscode.window.createStatusBarItem(
        vscode.StatusBarAlignment.Right,
        100
    );
    statusBarItem.text = '$(sync~spin) LogGazer';
    statusBarItem.tooltip = 'Checking LogGazer backend...';
    statusBarItem.command = 'loggazer.startBackend';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // ---- Commands ----

    // Analyze Terminal Selection
    const analyzeCmd = vscode.commands.registerCommand(
        'loggazer.analyzeTerminalSelection',
        () => analyzeTerminalSelection(context)
    );

    // Show Analysis Panel
    const showPanelCmd = vscode.commands.registerCommand(
        'loggazer.showAnalysisPanel',
        () => {
            const config = vscode.workspace.getConfiguration('loggazer');
            const apiUrl = config.get<string>('apiUrl', 'http://localhost:8000');
            AnalysisPanel.createOrShow(context.extensionUri, apiUrl);
        }
    );

    // Start Backend
    const startBackendCmd = vscode.commands.registerCommand(
        'loggazer.startBackend',
        () => startBackend(context)
    );

    context.subscriptions.push(analyzeCmd, showPanelCmd, startBackendCmd);

    // ---- Initial health check (deferred so editor startup isn't blocked) ----
    setTimeout(() => updateBackendStatus(context), 1000);

    // ---- Periodic health check (every 30s) ----
    healthCheckTimer = setInterval(() => updateBackendStatus(context), 30_000);
}

export function deactivate() {
    if (healthCheckTimer) {
        clearInterval(healthCheckTimer);
        healthCheckTimer = undefined;
    }
    console.log('LogGazer extension deactivated');
}

/**
 * Check backend health and update status bar indicator.
 *
 * States:
 *   $(circle-filled) green  → Backend healthy
 *   $(sync~spin) yellow     → Checking...
 *   $(circle-slash) red     → Backend unreachable
 */
async function updateBackendStatus(context: vscode.ExtensionContext): Promise<void> {
    const config = vscode.workspace.getConfiguration('loggazer');
    const apiUrl = config.get<string>('apiUrl', 'http://localhost:8000');

    statusBarItem.text = '$(sync~spin) LogGazer';
    statusBarItem.tooltip = 'Checking LogGazer backend...';
    statusBarItem.backgroundColor = undefined;

    const health = await LogGazerClient.checkHealth(apiUrl);

    if (health && health.status) {
        // Backend is healthy
        const status = health.status as string;
        if (status === 'healthy') {
            statusBarItem.text = '$(pass-filled) LogGazer';
            statusBarItem.tooltip = `LogGazer backend connected — ${apiUrl}`;
            statusBarItem.backgroundColor = undefined;
        } else if (status === 'degraded') {
            statusBarItem.text = '$(warning) LogGazer';
            statusBarItem.tooltip = `LogGazer backend degraded — ${apiUrl}\nClick to restart backend`;
            statusBarItem.backgroundColor = undefined;
        } else {
            statusBarItem.text = '$(error) LogGazer';
            statusBarItem.tooltip = `LogGazer backend unhealthy — ${apiUrl}\nClick to restart backend`;
            statusBarItem.backgroundColor = undefined;
        }
    } else {
        // Backend unreachable
        statusBarItem.text = '$(circle-slash) LogGazer';
        statusBarItem.tooltip = `LogGazer backend offline — Click to start (${apiUrl})`;
        statusBarItem.backgroundColor = new vscode.ThemeColor('statusBarItem.errorBackground');

        // Auto-start if configured
        const autoStart = config.get<boolean>('autoStartBackend', true);
        if (autoStart && !_autoStartAttempted) {
            _autoStartAttempted = true;
            // Only auto-start once per activation
            await startBackend(context);
        }
    }
}

let _autoStartAttempted = false;

/**
 * Start the LogGazer FastAPI backend in a VS Code terminal.
 *
 * Strategy:
 *   1. Check if backend is already running — if so, notify and return.
 *   2. Find the project root (where api/ and requirements.txt live).
 *   3. Create a dedicated "LogGazer Backend" terminal.
 *   4. Run `python -m api.main` in that terminal.
 */
async function startBackend(context: vscode.ExtensionContext): Promise<void> {
    const config = vscode.workspace.getConfiguration('loggazer');
    const apiUrl = config.get<string>('apiUrl', 'http://localhost:8000');
    const pythonCmd = config.get<string>('pythonCommand', 'python');

    // Check if backend is already running
    const health = await LogGazerClient.checkHealth(apiUrl);
    if (health && health.status) {
        vscode.window.showInformationMessage(
            `LogGazer backend is already running at ${apiUrl} (status: ${health.status}).`
        );
        return;
    }

    // Try to locate the project root.
    // Priority: 1) loggazer.projectPath setting  2) workspace folders  3) prompt user
    let projectPath = config.get<string>('projectPath', '');

    if (!projectPath) {
        const workspaceFolders = vscode.workspace.workspaceFolders;
        if (workspaceFolders && workspaceFolders.length > 0) {
            // Check if the workspace folder contains api/main.py
            for (const folder of workspaceFolders) {
                const apiMainUri = vscode.Uri.joinPath(folder.uri, 'api', 'main.py');
                try {
                    await vscode.workspace.fs.stat(apiMainUri);
                    projectPath = folder.uri.fsPath;
                    break;
                } catch {
                    // api/main.py not found in this folder
                }
            }
        }
    }

    if (!projectPath) {
        const selected = await vscode.window.showOpenDialog({
            canSelectFolders: true,
            canSelectFiles: false,
            openLabel: 'Select LogGazer project folder',
            title: 'Locate LogGazer project (folder containing api/main.py)',
        });
        if (!selected || selected.length === 0) {
            vscode.window.showWarningMessage(
                'Cannot start backend without the project path. Set loggazer.projectPath in settings.'
            );
            return;
        }
        projectPath = selected[0].fsPath;
    }

    // Create a dedicated terminal for the backend
    const terminal = vscode.window.createTerminal({
        name: 'LogGazer Backend',
        cwd: projectPath,
        message: 'Starting LogGazer FastAPI backend...',
    });

    // On Windows, use the full command; on Unix, use the same
    const isWindows = process.platform === 'win32';
    const activateCmd = isWindows
        ? ''  // venv activation is implicit or handled by .env
        : ''; // same for Unix

    // Send the start command
    if (activateCmd) {
        terminal.sendText(activateCmd);
    }
    terminal.sendText(`echo "Starting LogGazer backend at ${apiUrl}..."`);
    terminal.sendText(`${pythonCmd} -m api.main`);

    // Show the terminal briefly
    terminal.show(false); // false = don't focus, just reveal

    vscode.window.showInformationMessage(
        `LogGazer backend starting at ${apiUrl}... ` +
        `The terminal will stay open. Check the "LogGazer Backend" terminal for output.`
    );

    // Schedule a health check after a few seconds
    setTimeout(() => updateBackendStatus(context), 5_000);
    setTimeout(() => updateBackendStatus(context), 15_000);
}
