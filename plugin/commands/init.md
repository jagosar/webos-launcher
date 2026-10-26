---
description: Installs or updates agentic-flow in this repo (/architect, /implement, agents, workflow, guardrails and language rules). It leaves alone the flow files you have modified.
argument-hint: "[--dry-run] [--force]"
model: sonnet
---

# Role

You install the agentic flow in this session's repo, or update it if it is already there. A single deterministic command does the work. You run it, explain the result and say what to do next. Do not edit files by hand.

Arguments: $ARGUMENTS
- `--dry-run`: show what it would do, without writing anything.
- `--force`: also replace the flow files the user has modified.

Pass only those two arguments, and only if they appear above. Ignore any other text in the arguments.

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
   - If **"modified by you (left alone)"** appears, list those files. Explain that they were kept and that the user can compare them with the new version or repeat with `--force`.
4. Next steps for the user:
   - **Open a new session** in this repo, so Claude Code loads the commands, the agents, the hook and the workflow. If asked, they must **trust the folder**: without that, the project's permissions do not apply.
   - **On Pro**, enable **Dynamic workflows** in `/config`.
   - **Commit `.claude/`**, so CI and the team have the flow. Do not do it yourself.
   - **Start** with `/architect <what you want to build or change>`.

# Rules

- Run only the two commands above.
- Do not commit or push.
