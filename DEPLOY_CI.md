# CI/CD and Deployment Automation

GitHub Actions workflows live under `.github/workflows/`.

## Workflows

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci.yml` | Push and PR to `main` | Install deps and run `pytest` |
| `deploy.yml` | Manual (`workflow_dispatch`) | SSH deploy to EC2 and restart `planb-plus` systemd user service |
| `ml-retrain.yml` | Manual or weekly (Sunday 06:00 UTC) | Fetch data, train ML model, upload artifacts |

## Required GitHub secrets

Configure under **Settings → Secrets and variables → Actions** (and optional **Environments** for `production` / `staging`).

| Secret | Description | Example |
|--------|-------------|---------|
| `EC2_HOST` | EC2 public hostname or IP | `ec2-xx-xx.compute.amazonaws.com` |
| `EC2_USER` | SSH user on the instance | `ubuntu` |
| `EC2_SSH_KEY` | Private key (PEM) for deploy user | `-----BEGIN OPENSSH PRIVATE KEY-----...` |
| `DEPLOY_PATH` | Agent checkout path on EC2 | `/home/ubuntu/planb-plus` |

## Deploy workflow inputs

- **environment** — `production` or `staging` (maps to GitHub Environment for approval gates)
- **restart_only** — skip `git pull` and `pip install`; only restart systemd
- **skip_pre_live_check** — skip `pre_live_check.py` (emergency use only)

Deploy runs on EC2 where `.env`, TWAK keychain, and wallet secrets already exist. CI never receives trading credentials.

## ML retrain workflow

Runs the offline pipeline:

```bash
python scripts/fetch_historical_data.py --binance-only
python scripts/build_feature_matrix.py
python scripts/train_regime_model.py --allow-low-auc
python scripts/smoke_ml_inference.py
```

Artifacts (`models/*.pkl`, `validation_auc_v2.txt`, `MODEL_QUALITY_REPORT.md`) are retained for 30 days.

Enable **require_min_auc** on manual runs to fail when worst-fold AUC is below 0.65.

## EC2 prerequisites

Before the first automated deploy:

1. Clone repo to `DEPLOY_PATH` with a Python 3.12 venv
2. Copy `.env` and configure TWAK unlock (`verify_twak_unlock.py` must pass)
3. Install systemd user unit from `systemd/planb-plus.service`
4. Ensure `curl` and `git` are available on the host

## Manual verification

```bash
# After deploy workflow completes
curl -s http://localhost:8080/health | jq .
journalctl --user -u planb-plus -n 50
```
