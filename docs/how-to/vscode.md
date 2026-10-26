# Guide: Visual Studio Code (and Cursor)

**Goal:** use the flow from the official Claude Code extension, reviewing changes in the editor.
**Status:** not tested. This guide follows the extension's official documentation; the flow uses the same `.claude/` files as the CLI.

Before you start, meet the [requirements](README.md#requirements) and [install the flow in your project](README.md#install-the-flow-in-a-project).

## What this environment needs

- VS Code 1.94 or later. It also works in Cursor.
- The **Claude Code** extension by Anthropic (`anthropic.claude-code`).
- **Dynamic workflows enabled** (on Pro, in `/config`).
- **Optionally, the CLI installed separately**, for the fallback mode. The extension bundles a CLI for its panel, but does not put it on the `PATH`.

## 1. Install

1. Open Extensions (`Ctrl+Shift+X`, or `Cmd+Shift+X` on a Mac), search for **Claude Code** and click **Install**.
2. Open the panel with the **Spark** icon: in the editor toolbar (with a file open), in the Activity Bar, or from **✻ Claude Code** in the status bar.
3. Click **Sign in** and authorise in the browser with your plan's account. No API key needed.

## 2. Open the project

Use `File → Open Folder` on the repo root, where `.claude/` lives. Typing `/` in the panel should show `/architect` and `/implement`.

## 3. Use the flow

```
/architect Add PDF export to invoices
```

- No need to choose a model: each command sets its own. Click the model name under the input box to see the session's.
- The permission mode indicator under the input box works as in Desktop. Start with a mode that asks you.
- Read `architecture.md` in the editor and approve it.

```
/implement
```

- **Workflow:** follow it with `/workflows`, or from the agent counter, which opens the **agent map** with each agent, its state and its transcript.
- **Changes** are written straight to disk. Review them in **Source Control** (`Ctrl+Shift+G`).

## 4. Review and commit

- **Source Control** lists every file touched: code, `acceptance/` (wave 0), `qa/` and the documentation. The QA report is in the `/implement` summary and in `.claude/state/runs/workflow-result.json`.
- Commit and push from Source Control, or ask the session.

## Alternative: the CLI in the integrated terminal

Open the integrated terminal (`` Ctrl+` ``) and follow [terminal.md](terminal.md). When started from there, `claude` connects to the editor to show diffs.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `/implement` uses the fallback mode | Enable **Dynamic workflows** in `/config` and launch again |
| The flow's commands do not appear | Open the repo's root folder, not a subfolder |
| The architect's questions arrive as text instead of options | Answer in the chat: the architect accepts free-text answers |

## References

- [Claude Code in VS Code](https://code.claude.com/docs/en/vs-code)
