# Git history scrub — leaked secrets

**Status:** prepared, NOT executed. Requires force-push to `github/master` + reset of 5 worktrees. Run only after credentials are rotated (`credential-rotation.md` complete).

**Why not auto-run:** force-push to master is gated; rewriting history breaks every checkout including the 5 active worktrees:

```
/home/ulrich/Documents/Projects/jarvis-maya-speech                [feat/maya-speech]
/home/ulrich/Documents/Projects/jarvis/.worktrees/news-widget     [feature/news-widget]
/home/ulrich/Documents/Projects/jarvis/.worktrees/screen-watching [feat/screen-watching]
/home/ulrich/Documents/Projects/jarvis/.worktrees/voice-quality   [fix/voice-quality]
```

## Decision

**Skip it** if: the rotated keys are the only concern. The leaked strings in git history are now useless (revoked at the provider). Anyone with a clone holds dead bytes. Repo is private to you on GitHub, so the blast radius was always small.

**Do it** if: you want a clean repo for sharing later, or you're paranoid about a future provider that re-validates leaked-pattern keys.

## If you do it

```bash
cd /home/ulrich/Documents/Projects/jarvis

# 1. Make sure all worktrees are clean (commit or stash)
git worktree list
# (you'll have to manually visit each and `git status` / `git stash`)

# 2. Backup the entire repo (filter-repo is destructive)
cd ..
cp -r jarvis jarvis-pre-scrub-$(date +%F)
cd jarvis

# 3. Build the BFG-style replacement file
cat > /tmp/jarvis-secrets.txt <<'EOF'
APIveRsdNgLskjE==>REDACTED_LIVEKIT_KEY
KliwnenjqBuzWcjPDCrbo5aLbvTyz2pQZp490L7SjdT==>REDACTED_LIVEKIT_SECRET
REDACTED_GROQ_KEY==>REDACTED_GROQ_KEY
***REMOVED-LEAKED-KEY***==>REDACTED_DEEPSEEK_KEY
***REMOVED-LEAKED-KEY***==>REDACTED_LANGCHAIN_KEY
REDACTED_GOOGLE_KEY==>REDACTED_GOOGLE_KEY
697968751ando==>REDACTED_PG_PASSWORD
EOF

# 4. Run filter-repo. --force is required since the repo has a remote.
git-filter-repo --replace-text /tmp/jarvis-secrets.txt --force

# 5. filter-repo removes all remotes by default. Re-add and force-push.
git remote add github https://github.com/ulrichando/jarvis.git

# 6. Push backup branches first
git push github HEAD:secrets-scrubbed-backup-$(date +%F)

# 7. Verify on GitHub UI that the backup branch looks right.
#    Spot-check the old commits that contained .env / livekit.yaml.

# 8. THEN force-push the real branches. One at a time.
git push --force-with-lease github master
git push --force-with-lease github feat/ext-browser-control-v3
# (others as needed)

# 9. Reset every other worktree
for wt in /home/ulrich/Documents/Projects/jarvis-maya-speech \
          /home/ulrich/Documents/Projects/jarvis/.worktrees/news-widget \
          /home/ulrich/Documents/Projects/jarvis/.worktrees/screen-watching \
          /home/ulrich/Documents/Projects/jarvis/.worktrees/voice-quality; do
  echo "=== $wt ==="
  cd "$wt"
  git fetch github
  # rebase the worktree's branch onto the rewritten remote
  git rebase github/$(git rev-parse --abbrev-ref HEAD)
done

# 10. Cleanup
rm /tmp/jarvis-secrets.txt
```

## Recovery if something goes wrong

The `jarvis-pre-scrub-YYYY-MM-DD` directory is a full clone-equivalent. If history rewrite goes sideways:

```bash
cd /home/ulrich/Documents/Projects
mv jarvis jarvis-broken
mv jarvis-pre-scrub-* jarvis
```

…and the worktrees can be re-created.
