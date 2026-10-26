# Guide: JetBrains IDEs (IntelliJ IDEA, PyCharm, WebStorm, GoLand, PhpStorm, Android Studio)

**Goal:** use the flow from a JetBrains IDE, with diffs in the IDE's viewer.
**Status:** not tested. The plugin runs the same CLI, so the flow should behave as in the [terminal](terminal.md).

Before you start, meet the [requirements](README.md#requirements) and [install the flow in your project](README.md#install-the-flow-in-a-project).

## What this environment needs

- **The CLI installed.** The plugin does not bundle its own copy, so it is mandatory.
- The **Claude Code [Beta]** plugin from the JetBrains Marketplace.

## 1. Install

1. Install the CLI:

   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   ```

2. Go to `Settings → Plugins → Marketplace`, search for **Claude Code [Beta]**, install it and restart the IDE.

A Claude Code button appears in the IDE.

## 2. Open Claude Code

- Use `Ctrl+Esc` (`Cmd+Esc` on a Mac) or the Claude Code button.
- Or run `claude` in the integrated terminal, at the project root.
- From an external terminal, connect to the IDE with `/ide`.

Make diffs open in the IDE: run `/config` and set **Diff tool** to `auto`.

## 3. Use the flow

Exactly as in the [terminal](terminal.md):

```
/architect <your request>
```

```
/implement
```

- Changes are written straight to disk. Review them in the **Commit** window (`Alt+0`) or in **Git → Local Changes**.
- The QA report is in the `/implement` summary and in `.claude/state/runs/workflow-result.json`.

## Security

The official documentation warns that, in JetBrains IDEs, `acceptEdits` mode can modify IDE configuration files that the IDE executes, so it recommends Manual mode for edits.

- **In your interactive session:** follow that advice.
- **The flow's agents** use `acceptEdits`, but the hook only lets them write their own package's files, so they cannot touch `.idea/` or anything outside the manifest.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Cannot launch Claude Code" or "command not found" | Check `claude --version`. If the IDE cannot find it, set the full path in `Settings → Tools → Claude Code [Beta] → Claude command` |
| macOS: works in the terminal, not from the IDE | An IDE launched from the Dock may not inherit your shell's `PATH`. Set the full path in **Claude command** |
| Windows with WSL | In **Claude command**, use `wsl -d Ubuntu -- bash -lic "claude"`, with your distribution |
| "No available IDEs detected" on WSL2 | Follow the WSL section of the official guide (a firewall rule, or mirrored networking) |
| `Esc` does not interrupt | In `Settings → Tools → Terminal`, untick "Move focus to the editor with Escape" |

## References

- [Claude Code in JetBrains IDEs](https://code.claude.com/docs/en/jetbrains)
