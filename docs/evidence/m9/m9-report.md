# M9 — GitHub Actions CI

Date: 2026-11-04. **Status: workflow implemented and verified locally. The first run on GitHub
Actions is pending** — the repository has no remote and nothing has been pushed.

## Files

| File | Change |
|---|---|
| `.github/workflows/ci.yml` | **New** — the CI workflow (4 jobs) |
| `.gitattributes` | **New** — `* text=auto` (LF in the repository), `*.sh`/`*.tftpl` always LF, images binary |
| `docs/evidence/m9/` | **New** — outputs of the local checks and this report |

No M1–M8 file was modified.

## Workflow architecture

```text
on: push → main · pull_request → main · workflow_dispatch
permissions: contents: read        concurrency: cancel superseded runs per ref
runner: ubuntu-24.04 (pinned)      shell: bash

┌─ python-tests        (10 min)  setup-python 3.11 + pip cache → pip install -r app/requirements.txt → python -m pytest -q
├─ docker-build        (15 min)  compose --env-file .env.example --profile autoscale config --quiet → build app → build autoscaler
├─ terraform-validate  (15 min)  fmt -check → init -backend=false -lockfile=readonly → validate → tflint --init → tflint --recursive
└─ terraform-security  (10 min)  checkov -d terraform --framework terraform --skip-download (fails on any failed check)
```

| Job | Why it exists |
|---|---|
| python-tests | Runs all 134 tests (FastAPI app with SQLite, autoscaler logic with fakes). No Docker, database or network needed. |
| docker-build | Proves both images still build and the Compose file (including the opt-in `autoscale` profile) is valid. Nothing is started or pushed. |
| terraform-validate | The M8 static checks. `-lockfile=readonly` fails instead of silently changing the provider lock. |
| terraform-security | Checkov as a pass/fail gate; accepted findings are skipped inline with written reasons. |

Jobs are independent and run in parallel, so a failure names the area that broke.

## Supply-chain pinning (verified before use)

| Item | Pinned to | Verification |
|---|---|---|
| `actions/checkout` v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` | `git ls-remote` tag = GitHub API commit; commit signature `verified=True` |
| `actions/setup-python` v7.0.0 | `5fda3b95a4ea91299a34e894583c3862153e4b97` | same |
| `actions/cache` v6.1.0 | `55cc8345863c7cc4c66a329aec7e433d2d1c52a9` | same |
| `hashicorp/terraform:1.16.6` | `sha256:bfffc1de…b316` | registry (`imagetools`) = Docker Hub API = locally pulled digest |
| `ghcr.io/terraform-linters/tflint:v0.64.0` | `sha256:1c595f42…d19f` | registry = locally pulled digest |
| `bridgecrew/checkov:3.3.29` | `sha256:3cc125b5…69f3` | registry = Docker Hub API = locally pulled digest |
| `rhysd/actionlint:1.7.12` (local check only) | `sha256:b1934ee5…5667` | registry = Docker Hub API |

All third-party code is official GitHub actions or the same tool images used in M8; no other
marketplace actions.

## Security of the workflow

- `permissions: contents: read` for the whole workflow; `persist-credentials: false` on every checkout.
- **No repository secrets.** The automatic `GITHUB_TOKEN` (read-only) is exposed to exactly one step,
  `TFLint init`, to avoid GitHub API rate limits when downloading the AWS ruleset. It is passed by
  variable name (`-e GITHUB_TOKEN`), never on the command line, and GitHub masks it in logs.
- No AWS authentication, no `aws-actions`, no OIDC (`id-token`) permission; no `terraform plan`,
  `apply` or `destroy`. Terraform containers mount only `terraform/`; Checkov mounts it read-only.
- `CHECKPOINT_DISABLE=1` stops Terraform's version check call; Checkov uses `--skip-download`.
- Compose is validated with `.env.example` placeholders and `--quiet`, so no resolved values are printed.

## Local verification

| Check | Command (local) | Result | Evidence |
|---|---|---|---|
| Workflow syntax + shell scripts | `actionlint -no-color -verbose` (1.7.12, includes shellcheck 0.11.0) | ✅ 0 errors | [01-actionlint.txt](01-actionlint.txt) |
| Python tests | `python -m pytest -q` (Python 3.11.4) | ✅ 134 passed, 1 known deprecation warning | [02-pytest.txt](02-pytest.txt) |
| Compose config | `docker compose --env-file .env.example --profile autoscale config --quiet` | ✅ exit 0, no output | [03-compose-config.txt](03-compose-config.txt) |
| Image builds | `docker build --tag taskapi-app:ci ./app`, `… taskapi-autoscaler:ci ./autoscaler` | ✅ both exit 0 | [04-docker-build.txt](04-docker-build.txt) |
| Terraform fmt / init / validate | job commands on a **fresh copy of `terraform/` without `.terraform/`** | ✅ fmt exit 0; init installed `hashicorp/aws v6.71.0` from the lock file (lock unchanged); `Success!` | [05-terraform-validate.txt](05-terraform-validate.txt) |
| TFLint | `--init` with an **empty plugin cache**, then `--recursive` | ✅ ruleset aws 0.49.0 installed; 0 issues | [06-tflint.txt](06-tflint.txt) |
| Checkov | `-d /work --framework terraform --skip-download --compact --quiet` on the fresh copy | ✅ exit 0 — 174 passed, 0 failed, 20 skipped | [07-checkov.txt](07-checkov.txt) |

The fresh-copy runs reproduce what a GitHub runner sees after checkout (no `.terraform/`). Locally,
`tflint --init` ran **without** a token; on GitHub it receives the read-only `GITHUB_TOKEN`.

### `.gitattributes` impact (checked before and after creating it)

- All 17 tracked files were already stored with LF in git (`i/lf`); none stored with CRLF.
- After adding the file, `git hash-object --path` was compared with the index for every tracked
  file: only the 3 files with real M6–M8 edits differ, and **none differs only by line endings**.
- The working tree was not rewritten (tracked files remain `w/crlf` via `core.autocrlf=true`).
- A global `eol=lf` was deliberately not used, because it would change the Windows checkout.

### No disturbance of existing resources

A snapshot of all containers (names, IDs, states, start times, restart counts), project images and
the `pgdata` volume taken before the Docker checks was **identical** afterwards. PostgreSQL: 0 rows,
`tasks_id_seq` = 8113 (unchanged since M7). The only additions are the two `:ci` image tags.
The temporary fresh-copy workspace (incl. the 761 MB provider download) was deleted from the
scratch area after the checks.

## Still pending (requires GitHub)

- The actual first run on GitHub-hosted runners: Docker Hub / GHCR pulls, `actions/cache` behaviour,
  `GITHUB_TOKEN` handling, job timings and the status checks on a pull request.
- Prerequisites, in order: rotate the PostgreSQL password exposed during M7 P2 (never committed; it
  exists only in the local `.env`, which is git-ignored), commit M6–M9, add a remote, push.

## Known limitations

- **Python dependency drift:** `app/requirements.txt` uses version ranges, so CI may install newer
  packages than the local venv (e.g. fastapi 0.162.0, starlette 1.9.0). A constraints file was
  deliberately not added in M9.
- **Base images not pinned:** both Dockerfiles use `python:3.11-slim` by tag (M2/M7 files, unchanged).
- The AWS provider (~150 MB compressed) is downloaded on every `terraform-validate` run; only
  pip and TFLint plugins are cached.
- Docker Hub pull limits on shared runners can occasionally slow or fail an image pull.
- `autoscaler/tests/fakes.py` contains an intentionally fake database URL (`SuperSecret123`) used to
  prove that secrets never reach logs; GitHub secret scanning could flag it as a false positive.
- Branch protection (requiring the checks before merging) must be configured in GitHub settings
  once the repository exists there.

## Confirmations

- **No AWS resources were created, modified or accessed.** No AWS CLI, no credentials, no
  `terraform plan/apply/destroy`; no `.tfstate` or `.tfplan` files exist.
- **No commits, pushes or remotes.**
- `.env` was not read into any evidence or workflow file; scans found 0 credential values in
  `.github/`, `.gitattributes` and the M9 evidence.

## Interview explanation

> "I added a GitHub Actions pipeline with four parallel jobs: Python tests, Docker image builds with
> Compose validation, Terraform validate and lint, and a Checkov security gate. It runs on pushes
> and pull requests to main with read-only permissions. Every action is pinned to a verified commit
> SHA and every tool image to a digest — the same images I used locally, so CI and my evidence
> match. The Terraform checks stay static: no backend, a read-only lock file, no AWS credentials, and
> no plan or apply. Before relying on it, I reproduced each job locally on a fresh checkout and linted
> the workflow with actionlint; the only thing left is the first run on GitHub itself."
