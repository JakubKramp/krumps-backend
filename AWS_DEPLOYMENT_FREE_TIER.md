# AWS Deployment Runbook — Free-Tier (Single EC2)

A simplified, free-tier-friendly deployment: **one EC2 instance** running your existing
`docker-compose-prod.yml` stack (api, postgres, redis, celery-worker, celery-beat), accessed
directly over HTTP at the instance's public IP. No domain, no Fargate, no ALB, no NAT gateway,
no RDS, no ElastiCache.

This is essentially your current GCP-VM model, moved to AWS, with Playwright/DRI scraping enabled.

---

## What changed vs the Fargate design

| Fargate design | Free-tier design | Why |
|---|---|---|
| ECS Fargate services | 1× EC2 `t3.micro` | Free tier: 750 hrs/mo for 12 months = one box 24/7 |
| Application Load Balancer (~$18/mo) | direct HTTP on the box | No domain/TLS needed; access by public IP |
| NAT gateway (~$32/mo) | none (public subnet) | The box has a public IP; no private-subnet egress |
| RDS PostgreSQL | Postgres **container** | Already in your compose file |
| ElastiCache Redis | Redis **container** | Already in your compose file |
| Custom VPC + subnets | **default VPC** | One public subnet is all a single box needs |
| Route 53 + ACM cert | none | No domain — you reach it at `http://<elastic-ip>/` |

**Cost:** ~$0 for the first 12 months (no domain to buy). After the free year: roughly **$8–9/mo**
(t3.micro + small EBS + S3 pennies) vs ~$70+/mo for the Fargate stack.

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
   │   postgres   redis   celery-worker  beat  │
   │   (all docker containers, one compose)    │
   └───────────────────┬───────────────────────┘
                        │
                        ▼
                    S3 bucket  (dish images)
```

---

## Phase 0 — Prep

- **Region:** pick one (e.g. `eu-west-1`).
- **No domain needed** — you access the app at `http://<elastic-ip>/`.
- **Playwright is ENABLED** (`PLAYWRIGHT_ENABLED=true`). This requires:
  1. A **Dockerfile change** to install Chromium (Phase 1 below) — without it, DRI scraping crashes.
  2. Enough memory headroom on a 1 GB box (Phase 4 swap; consider the RDS offload if it's tight).
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

Create `cookbook-sg`:

| Port | Source | Purpose |
|---|---|---|
| 80 (HTTP) | `0.0.0.0/0` | app traffic |
| 22 (SSH) | **your IP only** | *optional* — admin access via SSH |

*Why:* one box, one SG. 80 is public for the app; Postgres/Redis are **not** exposed (reachable only
inside the Docker network). (No 443 since there's no TLS.)

> The port-22 rule is **optional** — with the `AmazonSSMManagedInstanceCore` policy from Phase 2 you
> can connect through **Session Manager** and leave SSH closed entirely. Add the SSH rule only if you
> also want key-based `ssh` access.

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
curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
# 4 GB swap (Chromium spikes memory during DRI scraping)
dd if=/dev/zero of=/swapfile bs=1M count=4096
chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

*Why the 4 GB swap:* baseline (postgres + redis + api + 2 celery ≈ 600 MB) already fills most of
1 GB; launching Chromium adds another 300–500 MB. DRI scraping only runs occasionally (a
`BackgroundTask` on profile creation), so the swap absorbs those spikes rather than OOM-killing the
box. If scraping is slow or still OOMs, use the RDS offload below.

---

## Phase 5 — Elastic IP

**EC2 → Elastic IPs → Allocate → Associate** with the instance.

*Why:* with no domain you reach the app **by IP**, so a stable IP matters — a plain public IP
changes on stop/start. An Elastic IP is fixed and free while associated with a running instance.

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

**SSH (only if you added the port-22 rule):**
```powershell
icacls "C:\path\to\cookbook-key.pem" /inheritance:r
icacls "C:\path\to\cookbook-key.pem" /grant:r "$($env:USERNAME):(R)"
ssh -i "C:\path\to\cookbook-key.pem" ec2-user@<elastic-ip>
```
(The `icacls` lines fix the Windows "unprotected private key file" error.)

---

## Phase 6 — Environment file

Put your production `config/.env` on the box (mounted by the api/worker/beat services). Set:
- `POSTGRES_HOST=postgres` (container name — DB stays on-box)
- `CELERY_BROKER_URL=redis://redis:6379/0`
- `CLOUD_PROVIDER=aws`, `AWS_BUCKET_NAME`, `AWS_REGION`
- **`PLAYWRIGHT_ENABLED=true`**
- `COOKBOOK_IMAGE=<acct>.dkr.ecr.<region>.amazonaws.com/cookbook-api:latest`
- `CORS_ALLOWED_ORIGINS` — include `http://<elastic-ip>` if a browser frontend calls the API
- plus `SECRET_KEY`, `POSTGRES_PASSWORD`, `NUTRITION_APIKEY`, and the rest from `config/env.example`.

Then expose the api on port 80 — in `docker-compose-prod.yml`, set the `api` service ports to:

```yaml
    ports:
      - "80:8000"
```

*Why:* on a single box a `chmod 600` `.env` is the simplest secret store. Mapping host 80 → container
8000 lets you reach the app at `http://<elastic-ip>/` with no reverse proxy.

---

## Phase 7 — Deploy

Your `ci.yml` already builds/pushes to ECR and SSHes into the box to run
`docker compose -f docker-compose-prod.yml pull && up -d`. Point the CI secrets `VM_IP` (the Elastic
IP), `VM_USER` (`ec2-user`), and `VM_SSH_KEY` at this instance. First deploy manually on the box:

```sh
cd ~/FastAPI_cookbook
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <acct>.dkr.ecr.<region>.amazonaws.com
docker compose -f docker-compose-prod.yml up -d
```

The entrypoint runs `alembic upgrade head` on api start, so migrations apply automatically.

---

## Phase 8 — Verify

1. `docker compose -f docker-compose-prod.yml ps` → all services **Up**.
2. `http://<elastic-ip>/health-check` → **200**.
3. Create a profile → confirm DRI scraping runs (check api logs for the Playwright job; Chromium
   should launch without a "browser not found" error — that verifies the Phase 1 Dockerfile change).
4. Upload a dish image → lands in S3, presigned URL loads.
5. `docker compose logs celery-beat` → schedule registered; `celery-worker` connected to Redis.

---

## Optional — offload the DB to RDS free tier (recommended with Playwright)

Chromium leaves little headroom on 1 GB. Moving Postgres off the box to a free-tier **`db.t3.micro`
RDS** (750 hrs/mo for 12 months) frees ~250 MB and makes scraping far more reliable. Remove the
`postgres` service from the compose file, point `POSTGRES_HOST` at the RDS endpoint, put the RDS in
the default VPC, and lock its security group to `cookbook-sg`. Still fully free-tier.

---

## Adding HTTPS later (when you get a domain)

To move off plain HTTP: register a domain, point an A record at the Elastic IP, add a `caddy`
service to the compose stack (`reverse_proxy api:8000`), change the api port mapping back to an
internal-only `8000`, and open 443 in the security group. Caddy fetches and auto-renews a free
Let's Encrypt cert with no extra config.

---

## Scaling up later

When you outgrow one box, `AWS_DEPLOYMENT.md` (the Fargate runbook) is the migration target:
RDS + ElastiCache + ECS services + ALB. The application code doesn't change between the two — only
the infrastructure around it.
