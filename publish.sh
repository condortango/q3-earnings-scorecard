#!/usr/bin/env bash
# Publishes ~/code/q3-earnings-scorecard to GitHub and turns on the scheduled updates.
# Run this only when you are ready to commit and push. It will:
#   1. make sure gh is signed in as the chosen account and has the 'workflow' scope
#   2. commit everything in the folder (first commit, or new changes)
#   3. create the GitHub repo if it does not exist (public), and push main
#   4. enable GitHub Pages with "GitHub Actions" as the source
#   5. start the first workflow run
# Usage (from WSL):  bash publish.sh [github-account] [repo-name]
# Defaults: account condortango (the account stocks-or-bonds uses), repo q3-earnings-scorecard.
set -euo pipefail
OWNER="${1:-${OWNER:-condortango}}"
REPO="${2:-${REPO:-q3-earnings-scorecard}}"
DIR="${DIR:-$HOME/code/q3-earnings-scorecard}"
WF=update.yml
cd "$DIR"
echo "Publishing $DIR to github.com/$OWNER/$REPO"

command -v gh >/dev/null || { echo "gh (GitHub CLI) is not installed. Stopping."; exit 1; }

# 1. gh must be signed in as OWNER
who=$(gh api user -q .login 2>/dev/null || true)
if [ "$who" != "$OWNER" ]; then
  echo "gh is signed in as '$who'; switching to $OWNER"
  gh auth switch -h github.com -u "$OWNER" || true
  who=$(gh api user -q .login 2>/dev/null || true)
  [ "$who" = "$OWNER" ] || { echo "Could not switch gh to $OWNER. Run: gh auth login (as $OWNER), then rerun. Stopping."; exit 1; }
fi
echo "gh user: $who"

# Pushing a file under .github/workflows needs the 'workflow' scope
status=$(gh auth status -h github.com --active 2>&1 || gh auth status -h github.com 2>&1 || true)
scopes=$(printf '%s\n' "$status" | awk -v u="$OWNER" '
  /Logged in to github.com/ { inblock = (index($0, "account " u " ") > 0 || index($0, "as " u " ") > 0) }
  inblock && /Token scopes:/ { print; exit }')
if ! printf '%s\n' "$scopes" | grep -Eq "(^|[^a-z:])'?workflow'?([^a-z:]|$)"; then
  echo "The gh token for $OWNER is missing the 'workflow' scope. Run this, then rerun publish.sh:"
  echo "  gh auth refresh -h github.com -s workflow"
  exit 1
fi
echo "gh token has the workflow scope"

# 2. Commit
[ -d .git ] || git init -b main
git checkout -B main >/dev/null 2>&1 || true
git add -A
if git diff --cached --quiet; then
  echo "Nothing new to commit"
else
  uid=$(gh api user -q .id)
  git -c user.name="$OWNER" -c user.email="$uid+$OWNER@users.noreply.github.com" \
    commit -q -m "Q3 2026 S&P 500 earnings scorecard: data, site and scheduled update"
  echo "Committed"
fi

# 3. Create the repo if needed, then push (gh supplies the git credentials)
gh auth setup-git >/dev/null 2>&1 || true
if gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
  if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "https://github.com/$OWNER/$REPO.git"
  else
    git remote add origin "https://github.com/$OWNER/$REPO.git"
  fi
  git push -u origin main
else
  git remote remove origin 2>/dev/null || true
  gh repo create "$OWNER/$REPO" --public --source . --remote origin --push \
    --description "S&P 500 Q3 2026 earnings scorecard (FactSet headline): beat rates, surprise and blended growth, updated twice each weekday"
fi
# 4. Pages from GitHub Actions
gh api -X POST "repos/$OWNER/$REPO/pages" -f build_type=workflow >/dev/null 2>&1 \
  || gh api -X PUT "repos/$OWNER/$REPO/pages" -f build_type=workflow >/dev/null \
  || echo "Could not set Pages to GitHub Actions. Set it in Settings > Pages > Source: GitHub Actions."
gh repo edit "$OWNER/$REPO" --homepage "https://$OWNER.github.io/$REPO/" >/dev/null 2>&1 || true
echo "Pages source: GitHub Actions"

# 5. First run (GitHub can take a few seconds to register a new workflow)
ok=0
for i in 1 2 3 4 5 6; do
  if gh workflow run "$WF" -R "$OWNER/$REPO" >/dev/null 2>&1; then ok=1; break; fi
  echo "Workflow not registered yet, retrying in 10s ($i/6)"
  sleep 10
done
if [ "$ok" = 1 ]; then echo "First run started"; else
  echo "Could not start the workflow yet. Start it later with: gh workflow run $WF -R $OWNER/$REPO"; fi

echo
echo "Optional: revenue numbers need a free Finnhub key stored as a repo secret. To add it, run"
echo "  gh secret set FINNHUB_API_KEY -R $OWNER/$REPO"
echo "(gh prompts for the value), then: gh workflow run $WF -R $OWNER/$REPO"
echo
echo "Repo:    https://github.com/$OWNER/$REPO"
echo "Actions: https://github.com/$OWNER/$REPO/actions/workflows/$WF"
echo "Pages:   https://$OWNER.github.io/$REPO/  (live after the first run finishes, about 2 to 4 minutes)"
