# Deploying Mathia on Oracle Cloud (Always Free)

This keeps to free-tier limits (Ampere VM + optional managed DB) and uses OCIR for images.

## 0) Build & push image to OCIR
1. Create an OCIR repo (e.g., `mathia-web`), get your tenancy namespace and region code (e.g., `phx.ocir.io`).
2. Create an Auth Token for your user.
3. On your dev machine (multi-arch):
   ```bash
   docker buildx build --platform linux/arm64 -t <region>.ocir.io/<namespace>/mathia-web:latest .
   echo '<auth-token>' | docker login <region>.ocir.io -u '<namespace>/<username>' --password-stdin
   docker push <region>.ocir.io/<namespace>/mathia-web:latest
   ```

## 1) Provision VM (fast path)
- Launch an Always Free ARM VM (Ubuntu 22.04). Assign public IP.
- Open ports: 80/443 (for proxy), 8000 (app, optional), 7233 (Temporal UI if needed), 6379 (Redis) should stay internal.

## 2) Prepare server
Copy repo (or the needed files) to the VM, including a production-safe `.env`.
Set `WEB_IMAGE=<region>.ocir.io/<namespace>/mathia-web:latest`.

Bootstrap (optional helper script):
```bash
scp -r . ubuntu@<vm_ip>:~/mathia
ssh ubuntu@<vm_ip>
cd ~/mathia
chmod +x scripts/oci-bootstrap.sh
WEB_IMAGE=<region>.ocir.io/<namespace>/mathia-web:latest \
OCIR_USERNAME='<namespace>/<username>' \
OCIR_PASSWORD='<auth-token>' \
scripts/oci-bootstrap.sh
```

If you prefer manual:
```bash
sudo apt update && sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER && newgrp docker
docker login <region>.ocir.io -u '<namespace>/<username>'
WEB_IMAGE=... docker compose -f docker-compose.oci.yml up -d
```

## 3) Environment
- Use `.env` copy with secure keys; set `DJANGO_ALLOWED_HOSTS` to your domain/IP.
- For HTTPS, run a reverse proxy (Caddy/Traefik/nginx) on the VM that terminates TLS and forwards to `web:8000`.
- For data persistence: Postgres data is on the VM volume (`postgres_data`). If you prefer managed DB, set `DATABASE_URL` and remove the `db` service.

## 4) Temporal
- The compose stack includes `temporal_worker`. For light use it can share this VM.
- If you use Temporal Cloud instead, set the client settings in `.env` and remove the temporal services.

## 5) DNS & TLS
- Point your domain to the VM public IP.
- Use Caddy/Traefik for automatic Let’s Encrypt; or provision certs and configure nginx manually.

## 6) Operations
- Start/stop: `docker compose -f docker-compose.oci.yml up -d` / `down`
- Migrate: `docker compose exec web python manage.py migrate`
- Logs: `docker compose logs -f web`
- Updates: push new image, then `docker compose pull && docker compose up -d`

## 7) Minimal OCI console steps
- IAM: make sure your user has `ObjectStorage`/`OCIR` and `Compute` permissions (root tenancy policies usually OK for personal tenancy).
- OCIR: create repo, auth token.
- Compute: launch VM, add ingress rules for 80/443 (and 8000 if testing without proxy).
- Optional: Object Storage bucket for uploads/static; wire via `.env` if you move off local storage.
