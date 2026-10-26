# Guide: automatic QA in GitHub Actions

**Goal:** have the QA agent check every pull request against `architecture.md`, without launching it by hand.
**Status:** not tested. The workflow follows the official authentication and GitHub Actions documentation.

**Why QA and not the rest:** the architect needs a conversation with you, and implementers work best under your supervision. QA can run on its own: it reads the design, tests, and returns a verdict.

## What you need

- A Pro, Max, Team or Enterprise plan.
- A **token from your subscription**, stored as a repository secret:
  1. Generate it on your machine. It opens a browser and prints a token valid for one year; it is not stored anywhere.

     ```bash
     claude setup-token
     ```

  2. In GitHub, go to `Settings → Secrets and variables → Actions → New repository secret`, name it `CLAUDE_CODE_OAUTH_TOKEN` and paste the token. Never paste it in a chat or commit it.
- `architecture.md`, with its manifest, on the pull request's branch.

The token is tied to **your** subscription: every run counts against your usage limits. For a team, the official documentation recommends a Console API key in an `ANTHROPIC_API_KEY` secret; in that case set `"auth": "inherit"` in `.claude/agentic.json`.

## The workflow

Create `.github/workflows/agentic-qa.yml`:

```yaml
name: agentic-qa
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
    paths-ignore: ["docs/**"]

permissions:
  contents: read

concurrency:
  group: agentic-qa-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  qa:
    # Branches of this repo only (never forks), never drafts; without architecture.md there is nothing to check.
    if: >-
      github.event.pull_request.head.repo.full_name == github.repository &&
      github.event.pull_request.draft == false &&
      hashFiles('architecture.md') != ''
    runs-on: ubuntu-latest
    timeout-minutes: 40
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      - uses: actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1 # v7.1.0
        with:
          node-version: "22"
      # Install your project's dependencies here (section 3 of architecture.md).
      - name: Install Claude Code (exact version from npm, no install scripts)
        run: |
          npm install --prefix "$RUNNER_TEMP/cc" --no-save --ignore-scripts --no-audit --no-fund @anthropic-ai/claude-code@2.1.302
          echo "$RUNNER_TEMP/cc/node_modules/@anthropic-ai/claude-code-linux-x64" >> "$GITHUB_PATH"
      - run: python3 .claude/bin/agentic.py validate
      # Deterministic, no secrets, no cost: the wave 0 acceptance tests must pass.
      - run: python3 .claude/bin/agentic.py acceptance --expect green
      - name: QA agent
        env:
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
        run: python3 .claude/bin/agentic.py run-qa
      - name: Report in the job summary
        if: always()
        run: |
          python3 -c "import json;print(json.load(open('.claude/state/runs/QA.json'))['result'])" >> "$GITHUB_STEP_SUMMARY" || true
      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
        if: always()
        with:
          name: qa-report
          path: |
            qa/
            .claude/state/runs/
```

What each non-standard part is for:

- **Actions pinned by SHA.** A tag like `@v7` can be moved to point at other code; a SHA cannot. The version goes in the comment. To update, resolve the new version's SHA with `gh api repos/<owner>/<action>/git/ref/tags/<version>`.
- **`permissions: contents: read` and `persist-credentials: false`.** The job cannot write to the repo, and the GitHub token is not left in the checkout, within reach of the code that runs.
- **Branches of this repo only.** The QA agent runs the pull request's tests with your subscription token in the environment, and a malicious pull request from a fork could try to read it. GitHub does not pass secrets to forks; this condition makes it explicit.
- **The token only in the QA step.** `validate` and the acceptance tests run the pull request's code with no secret in the environment.
- **Claude Code at an exact version, without install scripts.** It comes from npm at a pinned version, instead of `curl | bash` with whatever is latest, and with `--ignore-scripts`, so no package runs code when installed. The native binary ships in its own package (`claude-code-linux-x64`) and is used directly. Update the version on purpose, not by surprise.
- **`concurrency`.** If you push several times in a row, only the last run continues, which saves usage.
- **`timeout-minutes`.** The job's brake. The flow has its own `timeout_s` as well.

## What you get

- The job fails if acceptance fails (at no cost: no agent is launched), or if QA returns `FAIL` or `BLOCKER`, or writes outside `qa/`.
- The report appears in the job summary, and the QA tests can be downloaded as an artifact.
- CI does not commit the QA tests. To keep them, download the artifact and commit them yourself.

## The official alternative

The [`anthropics/claude-code-action@v1`](https://github.com/anthropics/claude-code-action) action, with its `claude_code_oauth_token` input, answers `@claude` mentions and runs `prompt` automations. This guide calls the script directly instead, because it applies the flow's guardrails, separates acceptance (deterministic) from QA (an agent), and reports the exit code and the report in the flow's format. You can also set up the action and its secret with `/install-github-app` from Claude Code.

## Before you rely on it

This workflow has not been run yet. First:

1. Try it on a test pull request.
2. Check in the log that QA uses your subscription: no API key warning should appear.
3. Adjust the triggers if you open many pull requests, for example to run only with a label.

## References

- [Claude Code GitHub Actions](https://code.claude.com/docs/en/github-actions)
- [Long-lived tokens](https://code.claude.com/docs/en/authentication#generate-a-long-lived-token)
