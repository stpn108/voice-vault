# Setup How-To (for the mentor)

End-to-end setup of a Zenplate project with a non-technical owner and the
test-gated deploy pipeline. Everything here is done once per project by
the mentor. The owner is not involved until step 5.

Estimated time: 30–45 minutes per project.

## 0. How it fits together

```
Owner ──describes──▶ Claude ──branch + PR──▶ GitHub ──"Tests" check (GitHub-hosted VM)
                                                │
                                   Owner presses Merge
                                                │
                                   "Auto-bump version" (VERSION += 0.1, pushes to main)
                                                │
                                   "Deploy" job ──▶ self-hosted runner ON THE SERVER
                                                        cd $DEPLOY_DIR && git pull && ./redeploy.sh
                                                          ├─ pytest in the app-tests container  ◀── the real gate
                                                          ├─ red  → stop, old version keeps running
                                                          └─ green → build, restart, verify health + commit
```

Tests run twice. On the pull request they run on a GitHub-hosted Ubuntu
VM (pip install, SQLite): fast feedback for Claude and a green/red badge
for the owner, but not the production environment. After the merge they
run **on the server**, inside the `app-tests` container built from the
same Dockerfile as the app: same image, same Python, same dependencies.
Only that run decides whether the new version is deployed.

## 1. GitHub repository

1. **Create the repository** from the Zenplate template
   (Use this template → Create a new repository). **Private.** A
   self-hosted runner executes repository code; on a public repository
   anyone's pull request could run on your server.
2. **Invite the owner**: Settings → Collaborators → Add people → role
   **Write**. Write is enough to merge pull requests; do not give Admin.
   The owner should keep GitHub e-mail notifications on (the default):
   the pipeline reports success and failure as a comment on their pull
   request, and GitHub mails that comment.
3. **Actions**: Settings → Actions → General → "Allow all actions and
   reusable workflows". Workflow permissions: "Read and write" (the
   version bump commits to `main`).
4. **Branch rules for `main`** (Settings → Branches → Add rule, pattern
   `main`):
   - ☑ Do not allow bypassing the above settings
   - ☑ Block force pushes
   - ☑ Do not allow deletions
   - ☐ Require a pull request — **leave off**
   - ☐ Require status checks — **leave off**

   Why off: the "Auto-bump version" workflow pushes a commit to `main`
   with the built-in `GITHUB_TOKEN`. Both "require a pull request" and
   "require status checks" reject that push. The pipeline does not stop
   over it (the bump logs a warning and Deploy still runs), but the
   version number then never advances, which breaks `./version.sh` and
   the release notes. Production is protected by the server-side test
   run in `redeploy.sh`, not by the Merge button: a red PR that gets
   merged fails on the server and nothing is deployed. Claude's rules
   forbid pushing to `main`; the owner has no terminal.

   Hardened variant (optional, more admin work): use a *ruleset* instead,
   require the `tests` check, and let the bump workflow push with a
   deploy key that is listed as a bypass actor. Not needed for a start.
5. **Claude Code access.** Either the owner uses Claude Code on the web
   (claude.ai/code): install the Claude GitHub app on this repository so
   Claude can push branches and open pull requests. Or the owner uses
   Claude Code locally: install `gh` and run `gh auth login` once on the
   owner's machine with the owner's GitHub account. Without either,
   Claude pushes the branch and the owner opens the PR via the
   "Compare & pull request" button GitHub shows — still no terminal.
6. `DEPLOY_DIR` variable: Settings → Secrets and variables → Actions →
   Variables → New repository variable. Name `DEPLOY_DIR`, value = the
   absolute path from step 2.3 below. You can set it after the server step.

## 2. Server

Assumes a Linux host with Docker Engine + Compose v2 and one **deploy
user** (e.g. `deploy`) that owns all project checkouts. All projects on
the server share that user.

1. Deploy user in the docker group, once per server:
   ```bash
   sudo usermod -aG docker deploy
   ```
2. Shared Docker network, once per server (Compose expects it):
   ```bash
   docker network create shared_net
   ```
3. Clone the project as the deploy user:
   ```bash
   sudo -iu deploy
   mkdir -p ~/projects && cd ~/projects
   git clone git@github.com:<owner>/<repo>.git <repo>
   cd <repo>
   pwd    # → this is DEPLOY_DIR
   ```
   The deploy user needs read access to the repository: a deploy key
   (Settings → Deploy keys, read-only) or the runner's own credentials.
4. Configuration:
   ```bash
   cp .env.example .env
   nano .env
   ```
   Set a strong `POSTGRES_PASSWORD`, put it into `DATABASE_URL` as well,
   and give every project on the server a **unique** `COMPOSE_PROJECT_NAME`
   and `PORTS_PREFIX` (e.g. `zenplate-a` / `10`, `zenplate-b` / `11`).
   Duplicates here are the only way two projects can interfere.
5. First start:
   ```bash
   ./redeploy.sh
   docker compose ps     # db, db-backup, app: running/healthy
   ```

## 3. Runner (one per project, on the same server)

A self-hosted runner is registered to **one repository**. Personal
accounts have no organization-level runners, so every project gets its
own runner directory and service. A runner only ever receives jobs from
its own repository, and the Deploy job only touches that project's
`DEPLOY_DIR`. Runners idle at ~100 MB RAM; five of them are fine.

1. GitHub: Settings → Actions → Runners → New self-hosted runner → Linux.
   GitHub shows a download block and a `config.sh` line with a fresh
   token. Run them **as the deploy user**, in a project-specific directory:
   ```bash
   sudo -iu deploy
   mkdir -p ~/actions-runner/<repo> && cd ~/actions-runner/<repo>
   # paste the "Download" block from GitHub here (curl + tar)
   ./config.sh --url https://github.com/<owner>/<repo> \
               --token <TOKEN FROM THE PAGE> \
               --name <repo>-deploy \
               --labels deploy \
               --unattended
   ```
2. Install as a service so it survives reboots:
   ```bash
   sudo ./svc.sh install deploy
   sudo ./svc.sh start
   sudo ./svc.sh status
   ```
3. GitHub: Settings → Actions → Runners shows `<repo>-deploy` as **Idle**.
4. Set the `DEPLOY_DIR` variable (step 1.6) if not done yet.

Repeat 3.1–3.4 for every further project. Never share one runner
directory between two repositories.

## 4. Verify the pipeline

1. On any machine with the repository: create a branch, change one line in
   `RELEASE_NOTES.md`, push, open a pull request. The **Tests** check
   appears on the PR.
2. Merge the PR on GitHub.
3. Actions tab: **Auto-bump version** runs, then **Deploy** runs on the
   runner. A minute later the merged PR gets a **✅ Deployed vX.Y**
   comment. On failure it gets a **❌ Deployment failed** comment with the
   last 60 log lines instead; if no PR can be found for the commit, an
   issue labelled `deploy-failed` is opened.
4. On the server: `./version.sh` shows repo and app on the same commit.

If Deploy never starts: the runner is offline (step 3.3) or Actions are
disabled (step 1.3). This is the one case that is silent for
the owner: a queued job produces no comment. GitHub cancels it after 24 h.
The owner's instruction is "no comment after 10 minutes → tell the mentor". If Deploy is red: open the run, the
failing step is either the test run or the post-deploy verification; the
old version is still running.

## 5. Owner onboarding

1. The owner opens Claude Code on the repository (web or local).
2. `project.md` carries an "ONBOARDING NOT DONE" banner, so Claude starts
   the `onboarding` interview on its own: what the project is, who
   builds it (sets mode `owner`), what it is not, the owner's vocabulary,
   the first requirement. Claude opens a pull request "Project
   onboarding"; the owner merges it. That first merge also exercises the
   pipeline once more.
3. Tell the owner the four things they ever do:
   - Describe what they want, in their own words.
   - Read Claude's plain-language read-back and say yes or no.
   - Open the pull request Claude sends, read "What changes for users",
     press **Merge** if the check is green.
   - Watch the **Actions** tab: green **Deploy** means it is live.
   Everything else is `.claude/operations.md`, top half.

## 6. Several projects on one server

| Must be unique per project | Where |
|----------------------------|-------|
| `COMPOSE_PROJECT_NAME` | `.env` — container and volume names |
| `PORTS_PREFIX` | `.env` — host ports |
| Checkout directory (`DEPLOY_DIR`) | server + repository variable |
| Runner directory and name | `~/actions-runner/<repo>`, `<repo>-deploy` |

Shared and fine to share: the deploy user, the docker group, the
`shared_net` network, the Docker daemon. Backups land in each project's
own `volumes/backups/`. A deploy in project A never touches project B:
different runner, different directory, different Compose project.

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Deploy job stays "Queued" | Runner offline or label missing | `sudo ./svc.sh status` in the runner dir; label must be `deploy` |
| Bump run shows a warning "Could not push the version bump" | Branch rules block the push; Deploy still ran | Step 1.4 |
| Tests green on PR, red on server | Environment difference (dependency, TZ, Docker) | Open the Deploy log; the server run is authoritative. Paste it to Claude. |
| "App runs '…', expected '…'. Image was NOT rebuilt." | Build used a cached or stale image | Re-run the Deploy job; if it repeats, `docker compose build --no-cache app` on the server |
| `network shared_net declared as external, but could not be found` | Step 2.2 skipped | `docker network create shared_net` |
| Owner cannot press Merge | Owner has Read, not Write | Step 1.2 |
