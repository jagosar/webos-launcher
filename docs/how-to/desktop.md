# Guide: Claude Code Desktop

**Goal:** use the flow from the **Code** tab of the Claude app.
**Status:** partly tested. The scripts, the agents and complete runs were launched from a Desktop session. The `/architect` interview with question cards has not been tested in the UI yet.

Before you start, meet the [requirements](README.md#requirements) and [install the flow in your project](README.md#install-the-flow-in-a-project).

## What this environment needs

- The Claude app for macOS, Windows or Linux (beta), signed in to a Pro, Max, Team or Enterprise plan.
- **Dynamic workflows enabled** (on Pro, in `/config`). The template already sets `"enableWorkflows": true` in `.claude/settings.json`.
- **Optionally, the CLI installed separately**, for the fallback mode. The app ships its own Claude Code, but does not put `claude` on the `PATH`.
- On Windows, use the **WSL** environment: `agentic.py` needs POSIX.

## 1. Open a session

1. Open the app and choose the **Code** tab, at the top.
2. Choose **Local** (or **WSL** on Windows), click **Select folder** and pick the repo.
3. **Model:** `/architect` asks for the latest Opus and `/implement` for the latest Sonnet. Before writing `architecture.md`, `/architect` tells you which model it is running on and asks whether to go ahead; if it is not Opus, choose **Pause to switch model**, pick Opus in the picker next to the send button and reply `continue`.
4. **Permission mode** (next to the send button):
   - **Manual** the first time, to see every step.
   - **Accept edits** once you know the flow.
   - **Auto** has not been tested with the flow: its classifier could hold back the launch of parallel agents.

Type `/`: `/architect` and `/implement` should appear.

## 2. Design

```
/architect A booking service with a REST API, Postgres and JWT authentication
```

- The questions appear as cards with options, the recommended one first.
- It ends with `Manifest valid` and a request for approval.
- Open `architecture.md` from the file pane (click its path) and read it. Once you approve, ask the session to "commit architecture.md".

## 3. Build

```
/implement
```

- The workflow runs in the background. The first time, the app shows an approval card (**Once**, **Always** or **Deny**), and the progress appears in the **Background tasks** pane, phase by phase, with each agent.
- Keep talking to the session while it runs; the app tells you when it finishes.
- For the raw logs, open the integrated terminal (**Ctrl+\`**) and run `python3 .claude/bin/agentic.py status`.

You get a report like the one in the [terminal guide](terminal.md#3-build): each package's status, the verification phases, review and QA, the fix cycles, the documentation and the proposed commits, waiting for your approval.

## 4. Review and deliver

- Click the `+N -M` indicator to open the **diff view**. You can comment on specific lines and Claude will address them.
- The QA report is in the `/implement` summary and, in full, in `.claude/state/runs/workflow-result.json`.
- For the commit, the push and the pull request, ask the session or use the app's buttons. The flow's agents cannot commit or push; the main session only does it when you ask.

## Tips

- **Several features at once:** open one session per feature from the sidebar, each in its own worktree.
- **From your phone:** turn on **Remote Control** to answer the architect's interview from your phone; the work keeps running on your machine.
- **Long jobs:** choose **Cloud** when creating the session so it keeps going if you close the app. See [web-ios.md](web-ios.md).
- **Check progress without waiting:** ask "how is it going?" and the launcher answers from the ledger.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `/implement` uses the fallback mode | Enable **Dynamic workflows** in `/config` and launch again |
| The app asks permission for `agentic.py` every time | Open `claude` once in the folder from a terminal and accept the trust prompt |
| `/architect` and the rest do not appear | Check that `.claude/commands/` is in the folder you selected |

## References

- [Desktop quickstart](https://code.claude.com/docs/en/desktop-quickstart)
- [Using Claude Code Desktop](https://code.claude.com/docs/en/desktop)
