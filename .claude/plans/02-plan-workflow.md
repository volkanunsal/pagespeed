# Plan: Add Worktree Workflow Rule to CLAUDE.md

## Context

The project needs a rule ensuring that plan implementations are isolated from `main` using git worktrees. This prevents in-progress work from polluting the main branch and makes it easy to abandon or restart work. The rule is scoped to plan implementations only — small ad-hoc changes (typos, config edits) can go directly on `main`.

## Change

Add a new `## Worktree Workflow` section to `/Users/newuser/w/work/projects/Otter/01-pagespeed-reports/CLAUDE.md`, placed between the existing "Plans" and "Config File" sections.

### Section Content

The section will cover:

1. **When**: Required before implementing any plan. Not required for small ad-hoc changes.
2. **Where**: `/tmp/worktrees/NNN-descriptive-name`
3. **Naming**: 3-digit zero-padded sequential number + kebab-case description (e.g., `001-add-lighthouse-support`). Branch name matches directory name. Next number determined by `git branch --list '[0-9]*'`.
4. **Lifecycle** (6 steps):
   - **Pre-flight**: Verify `git status --porcelain` is empty. If not, ask user to stash/commit/discard.
   - **Create**: `mkdir -p /tmp/worktrees && git worktree add -b NNN-name /tmp/worktrees/NNN-name main`
   - **Implement**: All file operations use absolute paths to worktree. Commits via `git -C /tmp/worktrees/NNN-name ...`.
   - **Merge**: From main working directory, `git merge NNN-name` (regular merge, not squash).
   - **Cleanup**: `git worktree remove /tmp/worktrees/NNN-name && git branch -d NNN-name`.
5. **Rules**:
   - Never commit plan implementation directly to `main`.
   - One worktree per plan.
   - Clean up stale worktrees with `git worktree remove --force` before creating new ones.
   - Plans are read from the main working directory; worktrees are for implementation only.

### Key Design Decisions

- **Local only**: No `git push` to remote. Branches stay local.
- **Regular merge** (not squash): Preserves per-step commit history.
- **3-digit numbering**: Matches user's example (`001-pagespeed`), gives room for growth.
- **Branch numbering via `git branch`**: Survives worktree cleanup, unlike `git worktree list`.
- **Scoped to plans only**: Ad-hoc fixes go directly on `main` without worktree overhead.

## Verification

After editing CLAUDE.md:
1. Read the file to confirm the new section is properly placed and formatted.
2. The rule is documentation-only — no code to test. Correctness is verified by reviewing the git commands in the lifecycle steps.
