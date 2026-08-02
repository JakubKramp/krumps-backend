# AWS Deployment Runbook — Free-Tier (Single EC2)

A simplified, free-tier-friendly deployment: **one EC2 instance** running your existing
`docker-compose-prod.yml` stack (api, redis, celery-worker, celery-beat) with **Postgres on a
free-tier RDS instance**, accessed directly over HTTP at the instance's public IP. No domain,
no Fargate, no ALB, no NAT gateway, no ElastiCache.

This is essentially your current GCP-VM model, moved to AWS, with Playwright/DRI scraping enabled.

---

## What changed vs the Fargate design

| Fargate design | Free-tier design | Why |
|---|---|---|
| ECS Fargate services | 1× EC2 `t3.micro` | Free tier: 750 hrs/mo for 12 months = one box 24/7 |
| Application Load Balancer (~$18/mo) | direct HTTP on the box | No domain/TLS needed; access by public IP |
| NAT gateway (~$32/mo) | none (public subnet) | The box has a public IP; no private-subnet egress |
| RDS PostgreSQL (`db.t4g.micro`) | RDS **`db.t3.micro`** | Free tier: 750 hrs/mo for 12 months; keeps Chromium from OOM-ing the 1 GB box |
| ElastiCache Redis | Redis **container** | Already in your compose file |
| Custom VPC + subnets | **default VPC** | One public subnet is all a single box needs |
| Route 53 + ACM cert | none | No domain — you reach it at `http://<elastic-ip>/` |

**Cost:** ~$0 for the first 12 months (no domain to buy). After the free year: roughly **$21–25/mo**
(t3.micro ~$8 + `db.t3.micro` RDS ~$12–15 + EBS + S3 pennies) vs ~$70+/mo for the Fargate stack.
The RDS instance is the biggest post-free-tier line item — set a billing alarm before month 12.

> ⚠️ **No HTTPS.** Without a domain there's no TLS, so traffic — including login credentials to the
> auth routes — travels in clear text. This is acceptable for testing/personal use. Before exposing
> it to real users, add a domain + Caddy (a 10-line change; see "Adding HTTPS later" at the bottom).

---

## Architecture

```
     Internet
        │  HTTP (80)
        ▼
   ┌──────────────────────────────────────────┐
   │  EC2 t3.micro  (default VPC, public IP)   │
   │                                           │
   │   api (uvicorn :8000, exposed on :80)     │
   │    │                                      │
   │    ├─ Playwright/Chromium (DRI scraping)  │
   │    │                                      │
   │   redis   celery-worker   celery-beat     │
   │   (docker containers, one compose file)   │
   └───────┬───────────────────────┬───────────┘
           │ 5432                  │
           ▼                       ▼
   ┌───────────────┐        S3 bucket (dish images)
   │ RDS Postgres  │
   │ db.t3.micro   │
   │ (private)     │
   └───────────────┘
```

---

## Phase 0 — Prep

- **Region:** pick one (e.g. `eu-west-1`).
- **No domain needed** — you access the app at `http://<elastic-ip>/`.
- **Playwright is ENABLED** (`PLAYWRIGHT_ENABLED=true`). This requires:
  1. A **Dockerfile change** to install Chromium (Phase 1 below) — without it, DRI scraping crashes.
  2. Enough memory headroom on a 1 GB box — the Phase 4 swap, plus Postgres living on RDS
     (Phase 5b) rather than competing with Chromium for the box's RAM.
- **S3 bucket** already exists (for `S3FileUploader`); S3 has a small 12-month free tier.

---

## Phase 1 — Dockerfile: install Chromium for Playwright

Add this line to the **final stage** of your `Dockerfile`, after `COPY --from=builder /app/.venv`:

```dockerfile
RUN playwright install --with-deps chromium
```

`--with-deps` also installs the OS libraries Chromium needs (runs `apt-get` at build time).

*Why:* `auth/dri_scrapper.py` launches Chromium via Playwright, but the current image only has the
`playwright` Python package — not the browser binary or its system deps. This bakes them in so
`PLAYWRIGHT_ENABLED=true` works out of the box.

> ⚠️ This affects **every** build (CI and your existing GCP prod too): the image grows by ~500 MB
> and builds take longer. That's the price of running Chromium in-container.

---

## Phase 2 — IAM role for the instance (ECR pull + shell access)

**IAM console → Roles → Create role → AWS service → EC2.** Name it `cookbookEc2Role` and attach:
- **`AmazonEC2ContainerRegistryReadOnly`** — lets the box pull the image from ECR.
- **`AmazonSSMManagedInstanceCore`** — enables **Session Manager**, a browser/CLI shell over AWS's
  backplane so you can connect without opening port 22 (see "Connecting to the box" after Phase 5).

*Why:* an instance role means no static AWS keys on the server (same no-keys pattern as
`S3FileUploader`). Scope in S3 access here too if you want the app's S3 calls to use the role instead
of `.env` credentials.

---

## Phase 3 — Security group (EC2 console → Security Groups, default VPC)

Create **two** SGs — `cookbook-sg` (the instance) and `cookbook-db-sg` (RDS). Create `cookbook-sg`
first, since the DB rule references it.

**`cookbook-sg`:**

| Port | Source | Purpose |
|---|---|---|
| 80 (HTTP) | `0.0.0.0/0` | app traffic |
| 22 (SSH) | `0.0.0.0/0` | CI deploy — see the warning below |

**`cookbook-db-sg`:**

| Port | Source | Purpose |
|---|---|---|
| 5432 | **`cookbook-sg`** | Postgres, from the app box only |

*Why:* 80 is public for the app; Redis is **not** exposed (it's `expose:`-only, reachable inside the
Docker network). RDS accepts connections only from anything wearing `cookbook-sg` — referencing the
SG rather than an IP means it keeps working if the instance is replaced. (No 443 since there's no TLS.)

> ⚠️ **Port 22 is open to the world** because `ci.yml` deploys via `appleboy/ssh-action` from
> GitHub-hosted runners, whose IPs are dynamic — you can't scope the rule to your own IP without
> breaking CI. Key-only auth (`PasswordAuthentication no`, the Amazon Linux default) is what's
> protecting it.
>
> To close port 22 entirely, switch the CI deploy step to `aws ssm send-command` over the OIDC role
> CI already uses for ECR, and connect yourself via **Session Manager** (Phase 2's
> `AmazonSSMManagedInstanceCore`). That removes the `VM_SSH_KEY` secret too.

---

## Phase 4 — Launch the EC2 instance

**EC2 console → Launch instance:**
- AMI: **Amazon Linux 2023** (or Ubuntu 24.04).
- Type: **`t3.micro`** (free-tier eligible).
- Key pair: create/select one for SSH.
- Network: **default VPC**, public subnet, **auto-assign public IP: enabled**.
- Security group: **`cookbook-sg`**.
- Storage: **gp3, 30 GB** (max free-tier allowance — the Playwright image is large).
- **IAM instance profile:** `cookbookEc2Role`.
- **User data** (installs Docker + a **4 GB swapfile** — larger because Chromium is memory-hungry):

```bash
#!/bin/bash
dnf update -y
dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
# 4 GB swap (Chromium spikes memory during DRI scraping)
dd if=/dev/zero of=/swapfile bs=1M count=4096
chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

*Why the 4 GB swap:* with Postgres on RDS the baseline is lighter (redis + api + 2 celery ≈ 350 MB),
but launching Chromium still adds 300–500 MB on a 1 GB box. DRI scraping runs occasionally (a
`BackgroundTask` on profile creation), so the swap absorbs those spikes rather than OOM-killing the
box.

---

## Phase 5 — Elastic IP

**EC2 → Elastic IPs → Allocate → Associate** with the instance.

*Why:* with no domain you reach the app **by IP**, so a stable IP matters — a plain public IP
changes on stop/start. An Elastic IP is fixed and free while associated with a running instance.

---

## Phase 5b — RDS PostgreSQL (free tier)

1. **RDS console → Create database → Standard create → PostgreSQL.**
   - Check the region offers **PostgreSQL 18** to match the `postgres:18.2` the stack used before;
     if only ≤17 is offered, take that and bump deliberately later.
2. Template **Free tier**. Instance **`db.t3.micro`**, 20 GB gp3, **storage autoscaling off**
   (autoscaling can silently carry you past the 20 GB free allowance).
3. Set master username + password — **save the password**, it becomes `POSTGRES_PASSWORD`.
4. **Connectivity:** **default VPC**, **Public access: No**, security group **`cookbook-db-sg`**
   (Phase 3). Leave "Connect to an EC2 compute resource" unset — the SG rule already covers it.
5. **Additional configuration → Initial database name:** `cookbook`. This is easy to miss, and
   without it RDS creates **no** database and the app fails to connect.
6. Turn **off** Enhanced Monitoring and Performance Insights (both can bill outside the free tier).
7. Create, wait for **Available**, then copy the **endpoint** → this is `POSTGRES_HOST`.

Verify from the box before deploying:

```sh
sudo dnf install -y postgresql17
psql -h <rds-endpoint> -U <master-user> -d cookbook -c 'select version();'
```

*Why:* Fargate-style managed Postgres on a box that also runs Chromium is the whole point of this
choice — it frees ~250 MB of RAM and, more importantly, your data survives the instance being
terminated, rebuilt, or resized. `entrypoint.sh` runs `alembic upgrade head` on api start, so the
schema is created on first deploy; you only need the empty database to exist.

> **Free-tier caveat:** the 750 hrs/mo RDS allowance covers **one** `db.t3.micro`. It also expires
> after 12 months, after which this instance is roughly **$12–15/mo** — the largest line item in the
> whole stack. Set a billing alarm.

---

## Phase 5c — SSH key for CI deploys

`ci.yml`'s `Deploy to EC2` step authenticates with a private key from the `VM_SSH_KEY` secret.
Generate a **separate, passphrase-less** key for CI rather than reusing your personal one:

```sh
ssh-keygen -t ed25519 -C "github-actions-cookbook" -f ~/.ssh/cookbook_ci -N ""
cat ~/.ssh/cookbook_ci.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
cat ~/.ssh/cookbook_ci        # copy this whole private key, BEGIN/END lines included
```

Then set the GitHub repo secrets (**Settings → Secrets and variables → Actions**):

| Secret | Value |
|---|---|
| `VM_IP` | the Elastic IP from Phase 5 |
| `VM_USER` | `ec2-user` |
| `VM_SSH_KEY` | the full private key printed above |
| `AWS_REGION`, `AWS_ROLE_ARN`, `ECR_REPOSITORY` | already used by the ECR push job |

*Why a dedicated key:* it's stored in a third party's secret store and used unattended, so it should
be revocable on its own — delete one line from `authorized_keys` and CI is cut off without touching
your own access. Passphrase-less is required because the action can't answer a prompt.

*Why not a passphrase-protected key:* there's nowhere to type the passphrase in a CI run; the
protection comes from the key being single-purpose and revocable instead.

---

## Connecting to the box

You need a shell on the instance to place the `.env` file and run the first deploy. Two options:

**Session Manager (recommended — no open SSH port):**
1. Confirm the instance has the `cookbookEc2Role` with **`AmazonSSMManagedInstanceCore`** (Phase 2).
   The SSM agent is preinstalled on Amazon Linux 2023.
2. **EC2 console → Instances →** select the instance **→ Connect → Session Manager tab → Connect.**
   A browser shell opens. No key file, no port 22.
3. Session Manager lands you as `ssm-user`; switch to the app user with `sudo su - ec2-user`.

*Why:* the shell runs over AWS's backplane (via the instance role), so you can keep port 22 closed
entirely — no SSH exposure to the internet.

**SSH (port 22 is open for CI — see Phase 3):**
```powershell
icacls "C:\path\to\cookbook-key.pem" /inheritance:r
icacls "C:\path\to\cookbook-key.pem" /grant:r "$($env:USERNAME):(R)"
ssh -i "C:\path\to\cookbook-key.pem" ec2-user@<elastic-ip>
```
(The `icacls` lines fix the Windows "unprotected private key file" error.)

---

## Phase 6 — Environment file

**Two separate files are needed** — this trips people up, because the compose file reads them
differently:

| File | Read by | Contains |
|---|---|---|
| `~/config/.env` **and** `~/FastAPI_cookbook/config/.env` | the **app** | all app settings |
| `~/FastAPI_cookbook/.env` | **compose itself** | `COOKBOOK_IMAGE`, `API_PORT` |

*Why two app copies:* the `api` service **bind-mounts** `~/config/.env` (an absolute path in your
home directory), while `celery-beat`/`celery-worker` use `env_file: config/.env` (relative to the
compose file). Same content, two locations. Keep them in sync — or symlink one to the other:
`ln -sf ~/config/.env ~/FastAPI_cookbook/config/.env`.

**App settings** (`~/config/.env`), from `config/env.example`:
- `POSTGRES_HOST=<rds-endpoint>` ← the Phase 5b endpoint, **not** `postgres`
- `POSTGRES_USER`, `POSTGRES_PASSWORD` (RDS master creds), `POSTGRES_DATABASE=cookbook`
- `CELERY_BROKER_URL=redis://redis:6379/0` (still a container, still by service name)
- `CLOUD_PROVIDER=aws`, `AWS_BUCKET_NAME`, `AWS_REGION`
- **`PLAYWRIGHT_ENABLED=true`**
- `CORS_ALLOWED_ORIGINS` — include `http://<elastic-ip>` if a browser frontend calls the API
- plus `SECRET_KEY`, `NUTRITION_APIKEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `APP_LOCATION`,
  `ALLOWED_EXTENSIONS`, `FILE_MAX_UPLOAD_SIZE`, `FILE_URL_EXPIRATION_SECONDS`.

**Compose settings** (`~/FastAPI_cookbook/.env`):

```sh
COOKBOOK_IMAGE=<acct>.dkr.ecr.<region>.amazonaws.com/cookbook-api:latest
API_PORT=80
```

Lock the app file down: `chmod 600 ~/config/.env`.

*Why a separate compose file:* `${COOKBOOK_IMAGE}` and `${API_PORT}` are **shell interpolation**,
resolved by compose before the container exists — compose only auto-loads `.env` from its own
directory, so putting them in `config/.env` leaves them empty and the image reference blank.

*Why `API_PORT=80`:* it maps host 80 → container 8000 so you reach the app at `http://<elastic-ip>/`
with no reverse proxy. It defaults to `8000` when unset, which is what keeps the existing GCP
deployment working off the same compose file.

---

## Phase 7 — Deploy

CI's deploy step does `cd ~/FastAPI_cookbook`, so the repo must be checked out at exactly that path.
Clone it first (secrets live in the `.env` files, not the repo, so a read-only clone is enough):

```sh
git clone https://github.com/JakubKramp/FastAPI_cookbook.git ~/FastAPI_cookbook
```

Then the first deploy, manually:

```sh
cd ~/FastAPI_cookbook
aws ecr get-login-password --region <region> \
  | docker login --username AWS --password-stdin <acct>.dkr.ecr.<region>.amazonaws.com
docker compose -f docker-compose-prod.yml pull
docker compose -f docker-compose-prod.yml up -d
```

The entrypoint runs `alembic upgrade head` on api start, so migrations apply to RDS automatically on
the first boot — no separate migration step.

*Note:* `docker login` credentials expire after 12 hours, but CI re-authenticates on every deploy, so
this manual login is only needed for the first run.

---

## Phase 8 — Verify

1. `docker compose -f docker-compose-prod.yml ps` → four services (`api`, `redis`, `celery-worker`,
   `celery-beat`) **Up**. There is deliberately **no** `postgres` container — it's RDS now.
2. `docker compose -f docker-compose-prod.yml logs api` → migrations ran, no connection errors.
   A hang here almost always means `cookbook-db-sg` isn't accepting from `cookbook-sg` (Phase 3).
3. `curl -i http://<elastic-ip>/health-check` → **200**.
4. Create a profile → confirm DRI scraping runs (check api logs for the Playwright job; Chromium
   should launch without a "browser not found" error — that verifies the Phase 1 Dockerfile change).
5. Upload a dish image → lands in S3, presigned URL loads.
6. `docker compose -f docker-compose-prod.yml logs celery-beat` → schedule registered;
   `celery-worker` connected to Redis.
7. Push to `master` → the CI deploy step succeeds over SSH (verifies Phase 5c end to end).

---

## Adding HTTPS later (when you get a domain)

To move off plain HTTP: register a domain, point an A record at the Elastic IP, add a `caddy`
service to the compose stack (`reverse_proxy api:8000`), drop `API_PORT` from `~/FastAPI_cookbook/.env`
so the api stops publishing on 80, and open 443 in the security group. Caddy fetches and auto-renews
a free Let's Encrypt cert with no extra config.

---

## Scaling up later

When you outgrow one box, `AWS_DEPLOYMENT.md` (the Fargate runbook) is the migration target:
RDS + ElastiCache + ECS services + ALB. The application code doesn't change between the two — only
the infrastructure around it.
