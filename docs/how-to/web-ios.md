# Guide: cloud sessions (web and iOS)

**Goal:** run the flow in a Claude Code cloud session, started from the browser, your phone, Desktop or the terminal.
**Status:** not tested. This guide follows the official documentation for cloud sessions.

Before you start, read the [requirements](README.md#requirements). Nothing needs installing on your machine.

## What a cloud session is

- It runs in an Anthropic virtual machine (Ubuntu 24.04, with Python 3, git and other tools preinstalled) on a clone of your GitHub repo.
- It keeps working when you close your laptop.
- It is available on Pro, Max and Team, counts against your plan's usage limits with no extra charge, and always uses your subscription.
- It loads the repo's `.claude/agents/` and `.claude/settings.json` (hooks and permissions), so the flow's guardrails apply here too.

## 1. Prepare

1. Connect GitHub, either way:
   - **In the browser:** install the **Claude GitHub App** during the first setup at [claude.ai/code](https://claude.ai/code).
   - **From the terminal:** run `/web-setup`, which reuses your `gh` login.
2. Push the branch with `.claude/` (and `architecture.md`, if it exists). The cloud clones what is on GitHub, not your local copy.

## 2. Start a session

Pick one:

- **Browser:** [claude.ai/code](https://claude.ai/code), then the repo and the branch.
- **iPhone:** the **Code** tab of the Claude app.
- **Desktop:** choose **Cloud** instead of **Local** when creating the session.
- **Terminal:**

  ```bash
  claude --cloud "/implement"
  ```

## 3. Use the flow

```
/architect <your request>
```

```
/implement
```

- **The cloud uses the native workflow**, as locally: workflows are available in cloud sessions and their progress is saved with the session. The fallback mode (`claude -p` processes) has not been tested inside the VM.
- Changes appear behind the `+N -M` indicator; open it to see the diff and comment on lines.
- When it finishes, create the pull request from the session.

## From your phone

The architect's interview suits a phone well: short questions with options. Two ways to use it:

- **Design on the phone, build on Desktop:** run `/architect` from the iPhone and, once you approve, `/implement` on Desktop. To continue a cloud session in your terminal: `claude --teleport`.
- **Everything on your machine:** start the session on Desktop or in the terminal and follow it from your phone with **Remote Control**.

## Troubleshooting

| Symptom | Fix |
|---|---|
| The session does not see `.claude/` or `architecture.md` | That branch was not pushed. The cloud clones the remote |
| No repositories appear | Check the GitHub connection (the App installed on the repo, or `/web-setup`) |
| Usage limits run out | Cloud sessions share limits with the rest of your usage. Run fewer packages per wave, or wait for the reset |

## References

- [Claude Code on the web](https://code.claude.com/docs/en/claude-code-on-the-web)
- [Cloud environments](https://code.claude.com/docs/en/cloud-environments)
- [Remote Control](https://code.claude.com/docs/en/remote-control)
