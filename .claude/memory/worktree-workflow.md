# Worktree Workflow

Required before implementing any plan. Not required for small ad-hoc changes (typos, config edits) — those can go directly on `main`.

## Naming

Worktree directories live under `/tmp/worktrees/`. Names use a 3-digit zero-padded sequential number + kebab-case description (e.g., `001-add-lighthouse-support`). The branch name matches the directory name. Determine the next number by inspecting existing branches:

```bash
git branch --list '[0-9]*'
```

## Lifecycle

1. **Pre-flight** — Verify the working tree is clean:

   ```bash
   git status --porcelain
   ```

   If output is non-empty, ask the user to stash, commit, or discard before proceeding.

2. **Commit plan** — After merging, commit the plan file to `main`:

   ```bash
   touch .claude/plans/NN-plan-name.md  # create the plan file first
   git add .claude/plans/NN-plan-name.md
   git commit -m "docs: add plan for ..."
   ```

3. **Create** — Create the worktree and branch from `main`:

   ```bash
   mkdir -p /tmp/worktrees
   git worktree add -b NNN-name /tmp/worktrees/NNN-name main
   ```

4. **Implement** — All file operations use absolute paths into the worktree. Commits go through `git -C`:

   ```bash
   git -C /tmp/worktrees/NNN-name add ...
   git -C /tmp/worktrees/NNN-name commit -m "..."
   ```

   Plans are read from the main working directory; worktrees are for implementation only.

5. **Merge** — From the main working directory, merge the branch (regular merge, not squash, to preserve per-step commit history):

   ```bash
   git merge NNN-name
   ```

6. **Cleanup** — Remove the worktree and delete the branch:
   ```bash
   git worktree remove /tmp/worktrees/NNN-name
   git branch -d NNN-name
   ```

## Rules

- Never commit plan implementation directly to `main`.
- One worktree per plan.
- Clean up stale worktrees before creating new ones: `git worktree remove --force /tmp/worktrees/NNN-name`.
- All branches stay local — no `git push` to remote.
