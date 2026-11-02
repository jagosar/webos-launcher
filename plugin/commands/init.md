---
description: Installs or updates agentic-flow in this repo (/architect, /implement, agents, workflow, guardrails and language rules), from any older version or a repo created from the template. It leaves alone the flow files you have modified.
argument-hint: "[--dry-run] [--force] [--allow-dirty]"
model: sonnet
---

# Role

You install the agentic flow in this session's repo, or update it if it is already there. A single deterministic command does the work. You run it, explain the result and say what to do next. Do not edit files by hand.

Arguments: $ARGUMENTS
- `--dry-run`: show what it would do, without writing anything.
- `--force`: also replace the flow files the user has modified.
- `--allow-dirty`: write even if a flow file has uncommitted changes.

Pass only those three arguments, and only if they appear above. Ignore any other text in the arguments.

# Why the flow is copied into the repo

The plugin is the installation and update channel. The flow lives in the repo, in `.claude/`, for four reasons:
- the permissions the agents need and the guardrail hook can only be declared in the project's `settings.json`;
- the workflow calls the agents by name (`implementer`, `qa`…), and a plugin would rename them with its prefix;
- CI, cloud sessions and the rest of the team need the flow even without the plugin;
- that way the flow's version stays pinned next to the code it produces.

# Steps

1. Run `git rev-parse --show-toplevel` to find the repo root.
   - If it is not a git repo, stop. Explain that the flow needs git to check which files each agent touches, and offer `git init`. Do not run it without a yes from the user.
2. Run the following, replacing `<root>` with that path and adding the allowed arguments:

   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/.claude/bin/agentic.py" install <root>
   ```

   - If `python3` does not exist, explain that the flow needs it (3.9 or later) and stop.
3. Summarise the output:
   - new, updated and removed files;
   - changes in `.claude/settings.json` and in `.gitignore`;
   - language rules installed.
   - If **"modified by you (left alone)"** appears, list those files. Explain that they were kept, that the new version of each is in `.claude/state/upgrade/<path>`, how to compare (`diff -ru .claude/state/upgrade/.claude .claude`), and that `--force` replaces them.
   - If the first line shows a version jump and **"upgrade notes:"** appears, relay each note: they say what may affect the user.
   - If **"validate: FAILED"** appears, say that the current `architecture.md` does not validate with the new flow and that `/architect` should revise it.
   - **Exit code 3** (`install: nothing was written`): a flow file has uncommitted changes, or a run is in progress. Relay the reasons. The user commits or stashes (or passes `--allow-dirty`), or stops the run with `python3 .claude/bin/agentic.py stop`. Do not do it yourself.
4. Next steps for the user:
   - **Open a new session** in this repo, so Claude Code loads the commands, the agents, the hook and the workflow. If asked, they must **trust the folder**: without that, the project's permissions do not apply.
   - **On Pro**, enable **Dynamic workflows** in `/config`.
   - **Commit `.claude/`**, so CI and the team have the flow. Do not do it yourself.
   - **Start** with `/architect <what you want to build or change>`.

# Rules

- Run only the two commands above.
- Do not commit or push.
