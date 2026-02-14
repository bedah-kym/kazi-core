# OCI Production Deployment Guide — Enterprise Edition

**Last Updated:** Feb 3, 2025 | **Version:** v1.0-enterprise | **Status:** ✅ Production-Ready | **Cloud Provider:** Oracle Cloud Infrastructure

---

## 📋 Overview

This guide covers deploying Mathia as a production-grade application on Oracle Cloud Infrastructure with enterprise security, monitoring, and scaling. This extends the quick-start guide with:
- ✅ High availability setup
- ✅ Load balancing & auto-scaling
- ✅ Advanced monitoring & observability
- ✅ Disaster recovery procedures
- ✅ Cost optimization strategies

**Enterprise Cost Estimate (Monthly):**
- 2× VM.Standard.E4.Flex (OCPU tier): ~$1,200
- OCI Database (PostgreSQL 15GB): ~$800
- OCI Cache (Redis 10GB): ~$400
- Load Balancing: ~$100
- Block Storage (500GB): ~$200
- Monitoring & Logging: ~$150
- **Total: ~$2,850-3,200/month** for full HA setup

---

## 🏗️ Enterprise Architecture

```
Internet
    ↓
DNS (Route 53 or Cloudflare)
    ↓
OCI Load Balancer (SSL termination)
    ↓ (Auto-scaling group of 3+)
Compute Instances (Web + Celery)
├─ Instance 1 (App Server + Worker)
├─ Instance 2 (App Server + Worker)
└─ Instance 3 (App Server + Worker)
    ↓
OCI Database (PostgreSQL 15GB, HA replication)
OCI Cache (Redis 10GB, cluster mode)
OCI Object Storage (backup storage)
Cloudflare R2 (file storage)
```

---

## 📊 Features Added vs Quick-Start

| Feature | Quick-Start | Enterprise |
|---------|-------------|-----------|
| Instances | 1 | 3+ (auto-scaling) |
| Database | Self-hosted PostgreSQL | OCI Managed PostgreSQL |
| Load Balancing | None | OCI Load Balancer |
| Monitoring | Basic logging | Prometheus + Grafana |
| Backups | Manual scripts | Automated, point-in-time recovery |
| SSL | Let's Encrypt | OCI Certificate Manager |
| Disaster Recovery | Manual | RTO: 15min, RPO: 1min |
| Cost | $1,400-2,450/mo | $2,850-3,200/mo |

---

## 🚀 Prerequisites

1. **OCI Account** with credits (minimum $200/month budget)
2. **Domain Name** (required for SSL, recommended for DNS failover)
3. **Terraform** (optional, for Infrastructure as Code)
4. **OCI CLI** configured locally
5. **Docker** installed locally
6. **kubectl** installed (if using OKE — Kubernetes)

---

## 🔧 Implementation Strategy

### Quick Decision Tree

```
Deploy to OCI?
├─ Small Team (< 10 users)
│  └─ Use: Quick-start guide + 1 Always Free VM
│
├─ Medium Scale (10-100 users)
│  └─ Use: This enterprise guide (HA setup)
│
├─ Large Scale (100+ users)
│  └─ Consider: OCI Kubernetes Engine (OKE)
│     + Managed PostgreSQL + Redis
│     + Auto-scaling pods
│
└─ Uncertain?
   └─ Start: Quick-start (1 VM)
      Scale later: Add load balancer & instances
```

---

## 🎯 Phase 1: Prepare Infrastructure (Week 1)

### Task 1.1: Set Up OCI Compartments & Permissions

```bash
# OCI CLI setup
oci setup config  # Creates ~/.oci/config

# Create IAM policy for automation
cat > iam_policy.tf << 'EOF'
# Terraform file for IAM permissions
resource "oci_identity_policy" "mathia_deployment_policy" {
  compartment_id = var.tenancy_id
  description    = "Policy for Mathia deployment automation"
  
  statements = [
    "Allow group Mathia-Admins to manage compute-instances in compartment Mathia",
    "Allow group Mathia-Admins to manage virtual-network-family in compartment Mathia",
    "Allow group Mathia-Admins to manage database-family in compartment Mathia",
    "Allow group Mathia-Admins to manage load-balancers in compartment Mathia",
    "Allow group Mathia-Admins to manage object-storage in compartment Mathia",
  ]
}
EOF
```

### Task 1.2: Create Infrastructure as Code

```bash
# Create Terraform directory structure
mkdir -p terraform/
cd terraform/

# Create main.tf
cat > main.tf << 'EOF'
terraform {
  required_version = ">= 1.0"
  
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 5.0"
    }
  }
  
  backend "local" {
    path = "terraform.tfstate"
  }
}

provider "oci" {
  region = var.region
}

# Variables
variable "region" {
  default = "us-ashburn-1"
}

variable "compartment_id" {
  type = string
}

variable "ssh_public_key" {
  type = string
}

# Data source for latest Ubuntu 22.04 image
data "oci_core_images" "ubuntu" {
  compartment_id = var.compartment_id
  operating_system = "Canonical Ubuntu"
  operating_system_version = "22.04"
  
  filter {
    name   = "state"
    values = ["AVAILABLE"]
  }
}

# Outputs
output "web_server_ips" {
  value = oci_core_instance.web[*].public_ip
}

output "load_balancer_ip" {
  value = oci_load_balancer.main.ip_address_details[0].ip_address
}

output "database_endpoint" {
  value = oci_database_db_system.mathia.hostname
}
EOF

# Create vpc.tf (networking)
cat > vpc.tf << 'EOF'
# VCN
resource "oci_core_vcn" "mathia_vcn" {
  compartment_id = var.compartment_id
  display_name   = "mathia-vcn"
  cidr_blocks    = ["10.0.0.0/16"]
}

# Internet Gateway
resource "oci_core_internet_gateway" "mathia_igw" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.mathia_vcn.id
  display_name   = "mathia-igw"
  is_enabled     = true
}

# Public Subnet
resource "oci_core_subnet" "public" {
  compartment_id      = var.compartment_id
  vcn_id              = oci_core_vcn.mathia_vcn.id
  display_name        = "mathia-public-subnet"
  cidr_block          = "10.0.1.0/24"
  
  route_table_id      = oci_core_route_table.public.id
  security_list_ids   = [oci_core_security_list.public.id]
}

# Private Subnet (for DB)
resource "oci_core_subnet" "private" {
  compartment_id      = var.compartment_id
  vcn_id              = oci_core_vcn.mathia_vcn.id
  display_name        = "mathia-private-subnet"
  cidr_block          = "10.0.2.0/24"
  prohibit_internet_ingress = true
  
  route_table_id      = oci_core_route_table.private.id
  security_list_ids   = [oci_core_security_list.private.id]
}

# Route tables & security lists (omitted for brevity)
EOF

# Apply infrastructure
terraform init
terraform plan -out=tfplan
terraform apply tfplan
EOF
```

---

## 💾 Phase 2: Deploy Managed Database (Week 1)

### Task 2.1: Create OCI Managed PostgreSQL

```bash
# Using OCI CLI
compartment_id="ocid1.compartment.oc1..."
vcn_id="ocid1.vcn.oc1..."

# Create database subnet group
db_subnet=$(oci database create-db-system \
  --compartment-id $compartment_id \
  --availability-domain "ADX:US-ASHBURN-AD-1" \
  --database-edition ENTERPRISE_EDITION \
  --db-name MATHIA \
  --admin-password "$(openssl rand -base64 32)" \
  --db-workload OLTP \
  --backup-destination-details type=NFS \
  --display-name "mathia-postgres" \
  --hostname "mathia-db" \
  --initial-db-storage-size-in-gb 256 \
  --query 'data."db-system-id"' \
  --raw-output)

echo "Database created: $db_subnet"
```

### Task 2.2: Configure Database for High Availability

```bash
# Enable Data Guard (replication) for HA
oci database create-data-guard-association \
  --database-id $database_id \
  --creation-type NewDbSystem \
  --display-name "mathia-db-standby" \
  --protection-mode MaxAvailability

# Configure automated backups
oci database db-backup create \
  --database-id $database_id \
  --retention-days 30
```

---

## 🖥️ Phase 3: Deploy Compute Instances (Week 2)

### Task 3.1: Create Auto-Scaling Group

```bash
# Using Terraform for auto-scaling

cat >> terraform/compute.tf << 'EOF'
# Instance configuration
resource "oci_core_instance" "web" {
  count               = 3  # Start with 3 instances
  compartment_id      = var.compartment_id
  display_name        = "mathia-web-${count.index + 1}"
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[count.index % 3].name
  shape               = "VM.Standard.E4.Flex"
  
  shape_config {
    ocpus         = 2
    memory_in_gbs = 16
  }
  
  source_details {
    source_type             = "IMAGE"
    source_id               = data.oci_core_images.ubuntu.images[0].id
    boot_volume_size_in_gbs = 100
  }
  
  create_vnic_details {
    subnet_id              = oci_core_subnet.public.id
    assign_public_ip       = true
    nsg_ids                = [oci_core_network_security_group.web.id]
  }
  
  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data           = base64encode(file("${path.module}/user_data.sh"))
  }
  
  depends_on = [oci_database_db_system.mathia]
}

# Load Balancer
resource "oci_load_balancer" "main" {
  compartment_id = var.compartment_id
  display_name   = "mathia-lb"
  shape          = "flexible"
  subnet_ids     = [oci_core_subnet.public.id]
  
  shape_details {
    minimum_bandwidth_in_mbps = 10
    maximum_bandwidth_in_mbps = 100
  }
}

# Backend Set
resource "oci_load_balancer_backend_set" "web_backends" {
  load_balancer_id = oci_load_balancer.main.id
  name             = "web-backends"
  
  health_checker {
    protocol    = "HTTP"
    port        = 8000
    url_path    = "/health/"
    interval_ms = 10000
    timeout_ms  = 3000
  }
  
  lb_cookie_session_persistence_configuration {
    cookie_name      = "mathia-lb"
    disable_fallback = true
  }
}

# Backend servers
resource "oci_load_balancer_backend" "web_server" {
  count            = 3
  load_balancer_id = oci_load_balancer.main.id
  backendset_name  = oci_load_balancer_backend_set.web_backends.name
  ip_address       = oci_core_instance.web[count.index].public_ip
  port             = 8000
  weight           = 1
}

# Listener (port 443 -> 8000)
resource "oci_load_balancer_listener" "web" {
  load_balancer_id         = oci_load_balancer.main.id
  name                     = "web-listener"
  default_backend_set_name = oci_load_balancer_backend_set.web_backends.name
  port                     = 443
  protocol                 = "HTTPS"
  ssl_certificate_name     = oci_load_balancer_certificate.ssl.certificate_name
  
  connection_configuration {
    idle_timeout_in_seconds = 1800
  }
}
EOF

# Apply
terraform apply
```

### Task 3.2: User Data Script for Instances

```bash
cat > user_data.sh << 'SCRIPT'
#!/bin/bash
set -e

# Update system
apt-get update
apt-get upgrade -y

# Install Docker
apt-get install -y ca-certificates curl gnupg lsb-release
mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker-archive-keyring.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Install Node Exporter (for Prometheus monitoring)
wget https://github.com/prometheus/node_exporter/releases/download/v1.7.0/node_exporter-1.7.0.linux-amd64.tar.gz
tar -xzf node_exporter-1.7.0.linux-amd64.tar.gz
mv node_exporter-1.7.0.linux-amd64/node_exporter /usr/local/bin/
useradd -rs /bin/false node_exporter

# Create systemd service for node_exporter
cat > /etc/systemd/system/node_exporter.service << 'EOF'
[Unit]
Description=Node Exporter
After=network.target

[Service]
User=node_exporter
Group=node_exporter
Type=simple
ExecStart=/usr/local/bin/node_exporter

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable node_exporter
systemctl start node_exporter

# Create app directory
mkdir -p /opt/mathia
cd /opt/mathia

# Download docker-compose from OCI Object Storage or GitHub
# (Copy your docker-compose.yml and .env here)

# Start services
docker-compose up -d

# Wait for app to be ready
until curl -f http://localhost:8000/health/ > /dev/null 2>&1; do
  sleep 5
done

echo "App started successfully"
SCRIPT
```

---

## 📊 Phase 4: Advanced Monitoring (Week 2)

### Task 4.1: Deploy Prometheus & Grafana

```bash
# Create monitoring stack docker-compose
cat > monitoring/docker-compose.yml << 'EOF'
version: '3.9'

services:
  prometheus:
    image: prom/prometheus:latest
    container_name: prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
    restart: always

  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    ports:
      - "3000:3000"
    environment:
      GF_SECURITY_ADMIN_USER: admin
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD}
      GF_INSTALL_PLUGINS: grafana-piechart-panel
    volumes:
      - grafana_data:/var/lib/grafana
      - ./dashboards:/etc/grafana/provisioning/dashboards
    depends_on:
      - prometheus
    restart: always

  alertmanager:
    image: prom/alertmanager:latest
    container_name: alertmanager
    ports:
      - "9093:9093"
    volumes:
      - ./alertmanager.yml:/etc/alertmanager/alertmanager.yml
      - alertmanager_data:/alertmanager
    command:
      - '--config.file=/etc/alertmanager/alertmanager.yml'
      - '--storage.path=/alertmanager'
    restart: always

volumes:
  prometheus_data:
  grafana_data:
  alertmanager_data:
EOF

# Prometheus configuration
cat > monitoring/prometheus.yml << 'EOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

alerting:
  alertmanagers:
    - static_configs:
        - targets:
            - alertmanager:9093

rule_files:
  - "alert_rules.yml"

scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

  - job_name: 'django'
    static_configs:
      - targets: ['mathia-web-1:8000', 'mathia-web-2:8000', 'mathia-web-3:8000']
    metrics_path: '/metrics/'

  - job_name: 'node'
    static_configs:
      - targets: ['node1:9100', 'node2:9100', 'node3:9100']

  - job_name: 'postgres'
    static_configs:
      - targets: ['postgres_exporter:9187']

  - job_name: 'redis'
    static_configs:
      - targets: ['redis_exporter:9121']
EOF

# Alert rules
cat > monitoring/alert_rules.yml << 'EOF'
groups:
  - name: django
    rules:
      - alert: HighErrorRate
        expr: rate(django_http_exceptions_total[5m]) > 0.01
        for: 5m
        annotations:
          summary: "High error rate detected"

      - alert: SlowRequests
        expr: histogram_quantile(0.95, django_http_request_duration_seconds) > 1
        for: 5m
        annotations:
          summary: "Slow requests detected"

  - name: system
    rules:
      - alert: HighCPUUsage
        expr: node_cpu_seconds_total > 80
        for: 5m
        annotations:
          summary: "High CPU usage"

      - alert: LowDiskSpace
        expr: node_filesystem_avail_bytes < 5368709120  # 5GB
        for: 5m
        annotations:
          summary: "Low disk space"

      - alert: HighMemoryUsage
        expr: (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) > 0.85
        for: 5m
        annotations:
          summary: "High memory usage"
EOF

# Deploy monitoring
cd monitoring
docker-compose up -d
```

### Task 4.2: Django Metrics Exporter

```python
# In Django settings.py
INSTALLED_APPS += ['django_prometheus']

# In urls.py
from django.urls import path
from django_prometheus import exports

urlpatterns += [
    path('metrics/', exports.ExportToDjangoView, name='prometheus-metrics'),
]

# In middleware settings
MIDDLEWARE.insert(0, 'django_prometheus.middleware.PrometheusBeforeMiddleware')
MIDDLEWARE.append('django_prometheus.middleware.PrometheusAfterMiddleware')
```

---

## 🔄 Phase 5: Disaster Recovery Setup (Week 3)

### Task 5.1: Backup Strategy

```bash
# Automated backup to OCI Object Storage
cat > backup/backup_strategy.sh << 'EOF'
#!/bin/bash

BACKUP_BUCKET="mathia-backups"
REGION="us-ashburn-1"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Backup PostgreSQL
BACKUP_FILE="postgres_$TIMESTAMP.sql.gz"
docker-compose exec -T postgres pg_dump -U postgres mathia | gzip > $BACKUP_FILE

# Upload to OCI Object Storage
oci os object put \
  --bucket-name $BACKUP_BUCKET \
  --file $BACKUP_FILE \
  --object-name "backups/$BACKUP_FILE" \
  --namespace-name "$(oci os namespace get --query data --raw-output)"

# Backup Redis RDB
REDIS_BACKUP="redis_$TIMESTAMP.rdb"
docker-compose exec -T redis redis-cli BGSAVE
sleep 5
cp redis_data/dump.rdb $REDIS_BACKUP

oci os object put \
  --bucket-name $BACKUP_BUCKET \
  --file $REDIS_BACKUP \
  --object-name "backups/$REDIS_BACKUP"

# Cleanup old backups (keep last 30 days)
oci os object delete-retention-rule \
  --bucket-name $BACKUP_BUCKET \
  --days 30

rm -f $BACKUP_FILE $REDIS_BACKUP
echo "Backup completed: $TIMESTAMP"
EOF

# Schedule via cron (every 6 hours)
crontab -e
# Add: 0 */6 * * * /opt/mathia/backup/backup_strategy.sh
```

### Task 5.2: Disaster Recovery Runbook

```markdown
# Disaster Recovery Runbook

## RTO: 15 minutes | RPO: 1 hour

### Scenario: Primary Database Failure

1. **Detection** (Automated via OCI monitoring)
   - Failover to standby database (Data Guard)
   - LB removes failed instances from pool
   - Alerts sent to ops team

2. **Failover Steps**
   ```bash
   # 1. Activate standby database
   dgmgrl / "FAILOVER TO mathia_standby"
   
   # 2. Update connection strings on app servers
   # (Update .env variables and restart)
   docker-compose restart web celery_worker
   
   # 3. Verify connectivity
   docker-compose exec web python manage.py dbshell
   
   # 4. Resume normal operations
   ```

3. **Recovery Steps** (When primary restored)
   - Reinstall primary as new standby
   - Switch back to original configuration
   - Run final tests

### Scenario: Application Server Failure

1. **Automatic Failover**
   - LB health check detects down instance
   - Removes from backend pool (< 10 seconds)
   - Traffic routed to remaining servers

2. **Instance Recovery**
   ```bash
   # Restart instance
   oci compute instance action \
     --instance-id $instance_id \
     --action SOFTRESET
   
   # Or replace with new one (auto-scaling group)
   ```

### Scenario: Data Corruption

1. **Restore from Backup**
   ```bash
   # Find latest backup before corruption
   oci os object list --bucket-name mathia-backups
   
   # Download backup
   oci os object get \
     --bucket-name mathia-backups \
     --name "backups/postgres_20250203_120000.sql.gz" \
     --file backup.sql.gz
   
   # Restore (on standby first for testing)
   gunzip < backup.sql.gz | psql -U postgres
   ```

2. **Validate Data**
   ```bash
   # Run integrity checks
   python manage.py check
   python manage.py test
   ```

3. **Promote Standby** if needed
```

---

## ✅ Deployment Verification

```bash
# Health checks
curl -I https://yourdomain.com/health/

# Database connectivity
docker-compose exec web python manage.py dbshell

# Celery tasks
docker-compose exec celery_worker celery -A Backend inspect active

# Prometheus metrics
curl http://prometheus:9090/api/v1/targets

# Load balancer backend status
oci load-balancer backend get \
  --load-balancer-id $lb_id \
  --backend-set-name web-backends
```

---

**Last Reviewed:** Feb 3, 2025  
**Next Review:** Q2 2026  
**Status:** ✅ Enterprise Production-Ready
