# Guide: install and update the flow with the plugin

> Reviewed on 2026-10-19 with Claude Code 2.1.302. What every environment shares is in the [guides overview](README.md).

The `agentic-flow` plugin brings the flow to an existing repo and keeps it up to date. For a new project, the template is still the fastest route (`gh repo create --template`).

## What it does, and what it doesn't

The plugin has a single command, `/agentic-flow:init`, which **copies the flow into the repo** (`.claude/`) and updates it when a new version ships. The agents, the workflow and the hook run from the repo, not from the plugin, for four reasons:

1. **Permissions and the hook.** The permissions the agents need and the guardrail hook can only be declared in the project's `settings.json`. A plugin cannot grant permissions.
2. **Names.** The workflow calls the agents by name (`implementer`, `qa`…), and a plugin would prefix them (`agentic-flow:implementer`).
3. **Team, CI and cloud.** CI, cloud sessions and your teammates need the flow even without the plugin.
4. **Reproducibility.** The flow's version stays pinned next to the code it produces.

Uninstalling the plugin does not remove the flow from the repo.

## What you need

- Claude Code 2.1.275 or later to install in one step; with older versions, use the two steps below.
- Read access to `jagosar/agentic-flow`. It is a private repo cloned with git, so your git must be able to authenticate with GitHub (for example with `gh auth login` and `gh auth setup-git`).
- `python3` 3.9+ and `git`, like the rest of the flow.

## Install

In a Claude Code session inside your repo:

```
/plugin install agentic-flow --marketplace jagosar/agentic-flow
```

Or in two steps, which also work from a terminal:

```bash
claude plugin marketplace add jagosar/agentic-flow
```

```bash
claude plugin install agentic-flow@agentic-flow
```

Then, in the session:

```
/agentic-flow:init
```

`/agentic-flow:init`:
- **Copies the flow** to `.claude/`: agents, commands, workflow, `agentic.py` and the rules library. The flow's own tests are not copied.
- **Merges your `.claude/settings.json`:** adds the permissions, the `deny` rules, the hook and `enableWorkflows`, removing nothing of yours.
- **Adds configuration:** creates `.claude/agentic.json` if missing and adds `.claude/state/` and `.agentic-result.json` to `.gitignore`.
- **Installs rules:** detects your languages and installs their rules in `.claude/rules/agentic/`.
- **Keeps a record:** stores the version and the fingerprint of every installed file in `.claude/agentic-install.json`.

`--dry-run` shows what it would do without writing anything. This is the installer's real output in a TypeScript repo, first install and a second run:

<p align="center"><img src="../assets/screens/install.png" width="760" alt="Real output of the installer: 23 new files, the settings.json additions, the .gitignore lines, the default config and the common and TypeScript rules; a second run reports everything unchanged."></p>

Then:
1. **Open a new session** in the repo and accept the trust prompt. Without it, Claude Code ignores the project's permissions.
2. **On Pro, enable Dynamic workflows** in `/config`.
3. **Commit `.claude/`.**
4. **Start** with `/architect`.

## Update

```bash
claude plugin update agentic-flow@agentic-flow
```

Restart the session and run `/agentic-flow:init` again. Each of the flow's files ends in one of these states:

| State | What happens |
|---|---|
| new | It did not exist: copied |
| updated | Changed in the new version and untouched by you: replaced |
| unchanged | Already up to date |
| modified by you (left alone) | You edited it since the last install: kept and listed. Compare it with the new version, or run again with `--force` to replace it |
| removed | The new version no longer ships it and you had not touched it: deleted |

The installer knows what you touched by comparing each file with the fingerprint in `.claude/agentic-install.json`. A file with no record (from a copy by hand, say) that differs from the new one counts as modified: it is not overwritten without `--force`.

## Try it without installing

To try a local copy of the plugin in a single session, without touching your configuration:

```bash
claude --plugin-dir /path/to/agentic-flow
```

## Publish a version (maintainers)

1. Bump `version` in `.claude-plugin/plugin.json`. The version lives only there; the marketplace entry does not repeat it.
2. Check the plugin, as CI does:

   ```bash
   claude plugin validate --strict .
   ```

3. Create the `agentic-flow--v<version>` tag:

   ```bash
   claude plugin tag .
   ```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `/plugin install` cannot find the marketplace | The repo is private and git cannot authenticate. Set up your GitHub credentials for git and try again |
| `/agentic-flow:init` does not appear | Plugins load when a session starts: restart it after installing or updating |
| After `init`, `/architect` does not appear | Open a new session in the repo: project commands and agents load at startup |
| `modified by you` on files you don't remember touching | The flow was copied by hand earlier, without a record. Check the difference and use `--force` if nothing of yours is there |
