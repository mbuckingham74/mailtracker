# Personal Email Tracking System - Implementation Plan

## Project Overview
Build a self-hosted email tracking pixel system at `https://mailtrack.tachyonfuture.com`

**Server:** Hostinger KVM8 VPS
**Domain:** mailtrack.tachyonfuture.com (A record configured, DNS propagated)

---

## Deployment Access

```
SSH: michael@tachyonfuture.com
Auth: Private key with fingerprint prompt
```

---

## Architecture

### Tech Stack
- **Backend:** FastAPI (Python 3.11+)
- **Database:** MySQL 8 (existing container - credentials in CLAUDE.md)
- **Reverse Proxy / SSL:** Nginx Proxy Manager (existing - credentials in CLAUDE.md)
- **Process Manager:** systemd

### Directory Structure
```
/opt/mailtrack/
├── app/
│   ├── __init__.py
│   ├── main.py           # FastAPI application
│   ├── database.py       # SQLAlchemy models & connection
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── pixel.py      # Tracking pixel endpoint
│   │   ├── api.py        # REST API for dashboard
│   │   └── dashboard.py  # Web UI routes
│   ├── templates/
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── create.html
│   │   └── detail.html
│   └── static/
│       ├── pixel.gif     # 1x1 transparent GIF (43 bytes)
│       └── style.css
├── venv/
├── requirements.txt
└── .env
```

---

## Database Schema

**Database name:** `mailtrack` (create this database in MySQL)

### Table: `tracked_emails`
```sql
CREATE TABLE tracked_emails (
    id VARCHAR(36) PRIMARY KEY,          -- UUID for tracking (used in pixel URL)
    recipient VARCHAR(255),               -- Email recipient (for your reference)
    subject VARCHAR(500),                 -- Email subject (for your reference)
    notes TEXT,                           -- Optional notes
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### Table: `opens`
```sql
CREATE TABLE opens (
    id INT AUTO_INCREMENT PRIMARY KEY,
    tracked_email_id VARCHAR(36) NOT NULL,
    opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    ip_address VARCHAR(45),               -- Supports IPv6
    user_agent TEXT,
    referer TEXT,
    country VARCHAR(100),                 -- GeoIP country (optional)
    city VARCHAR(100),                    -- GeoIP city (optional)
    FOREIGN KEY (tracked_email_id) REFERENCES tracked_emails(id) ON DELETE CASCADE,
    INDEX idx_tracked_email_id (tracked_email_id),
    INDEX idx_opened_at (opened_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

---

## API Endpoints

### Tracking Pixel
```
GET /p/{tracking_id}.gif
```
- Returns 1x1 transparent GIF
- Logs open to database
- Headers: Cache-Control: no-cache, no-store, must-revalidate
- Should return 200 even if tracking_id not found (don't leak info)

### Dashboard Web UI
```
GET /                     # Dashboard - list all tracked emails
GET /create               # Form to create new tracking pixel
POST /create              # Handle form submission
GET /detail/{id}          # View opens for specific email
```

### REST API (for future n8n integration)
```
GET  /api/tracks                    # List all tracked emails
POST /api/tracks                    # Create new tracking pixel
GET  /api/tracks/{id}               # Get single track with opens
GET  /api/tracks/{id}/opens         # Get just opens for a track
DELETE /api/tracks/{id}             # Delete tracking (and opens)
GET  /api/stats                     # Summary statistics
```

### Utility
```
GET /health                         # Health check endpoint
```

---

## Requirements (requirements.txt)

```
fastapi==0.109.0
uvicorn[standard]==0.27.0
sqlalchemy==2.0.25
aiomysql==0.2.0
pymysql==1.1.0
cryptography==42.0.0
python-dotenv==1.0.0
jinja2==3.1.3
python-multipart==0.0.6
```

---

## Authentication

Simple approach for personal use:
- **API Key** in header: `X-API-Key: {your-secret-key}`
- **Dashboard:** HTTP Basic Auth via Nginx (or session-based)
- Store API key in `.env` file

---

## Implementation Steps for Claude Code

### Phase 1: Server Setup
```bash
# 1. SSH into VPS
ssh michael@tachyonfuture.com

# 2. Create directory structure
sudo mkdir -p /opt/mailtrack/app
sudo chown -R michael:michael /opt/mailtrack

# 3. Install system dependencies (if not present)
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip

# 4. Create Python virtual environment
cd /opt/mailtrack
python3.11 -m venv venv
source venv/bin/activate

# 5. Install Python dependencies
pip install fastapi uvicorn[standard] sqlalchemy aiomysql pymysql python-dotenv jinja2 python-multipart cryptography

# 6. Create MySQL database and tables
# Connect to MySQL container and run:
#   CREATE DATABASE mailtrack;
#   Then run the CREATE TABLE statements from the schema section
```

### Phase 2: Core Application
Create these files in order:

1. **requirements.txt**
2. **app/database.py** - SQLAlchemy async setup with MySQL, models
3. **app/routes/pixel.py** - The tracking pixel endpoint (most critical)
4. **app/routes/api.py** - REST API endpoints
5. **app/routes/dashboard.py** - Web UI
6. **app/templates/*.html** - Jinja2 templates
7. **app/static/pixel.gif** - 1x1 transparent GIF
8. **app/static/style.css** - Minimal styling
9. **app/main.py** - FastAPI app assembly
10. **.env** - Configuration (MySQL connection string, API key)

### Phase 3: Deployment Configuration

1. **systemd service** - `/etc/systemd/system/mailtrack.service`
2. **Nginx Proxy Manager** - Add proxy host:
   - Domain: `mailtrack.tachyonfuture.com`
   - Scheme: `http`
   - Forward Hostname/IP: `127.0.0.1` (or server's internal IP if NPM is containerized)
   - Forward Port: `8000`
   - Enable SSL with Let's Encrypt
   - Force SSL: Yes
   - HTTP/2 Support: Yes

### Phase 4: Testing
1. Create a test tracking pixel via dashboard
2. Open the pixel URL in browser
3. Verify open is logged in MySQL
4. Test from actual email client

---

## Nginx Proxy Manager Configuration

Access NPM admin panel (credentials in CLAUDE.md) and create a new Proxy Host:

**Details Tab:**
- Domain Names: `mailtrack.tachyonfuture.com`
- Scheme: `http`
- Forward Hostname / IP: `127.0.0.1` (or host IP if NPM runs in Docker)
- Forward Port: `8000`
- Cache Assets: Off
- Block Common Exploits: On
- Websockets Support: Off

**SSL Tab:**
- SSL Certificate: Request a new SSL Certificate
- Force SSL: Yes
- HTTP/2 Support: Yes
- HSTS Enabled: Yes

**Advanced Tab (optional):**
```nginx
# Ensure real IP is passed to FastAPI
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

# No caching for pixel endpoint
location /p/ {
    proxy_pass http://127.0.0.1:8000;
    add_header Cache-Control "no-cache, no-store, must-revalidate";
    add_header Pragma "no-cache";
    add_header Expires "0";
}
```

---

## systemd Service

```ini
[Unit]
Description=Mailtrack FastAPI Application
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/mailtrack
Environment="PATH=/opt/mailtrack/venv/bin"
EnvironmentFile=/opt/mailtrack/.env
ExecStart=/opt/mailtrack/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## Environment Variables (.env)

```bash
# API authentication
API_KEY=your-secure-random-key-here

# MySQL connection (get credentials from CLAUDE.md)
DATABASE_URL=mysql+aiomysql://root:PASSWORD_FROM_CLAUDE_MD@localhost:3306/mailtrack

# App settings
DEBUG=false
```

**Note:** If MySQL is running in Docker, replace `localhost` with the container IP or use Docker networking (e.g., `host.docker.internal` or the container name if on same Docker network).

---

## HTML Snippet Generator

When creating a new tracking pixel, the dashboard should provide copy-paste snippets:

**HTML (for rich emails):**
```html
<img src="https://mailtrack.tachyonfuture.com/p/{ID}.gif" width="1" height="1" style="display:none" alt="" />
```

**Markdown (for reference):**
```markdown
![](https://mailtrack.tachyonfuture.com/p/{ID}.gif)
```

---

## Optional Enhancements (Future)

1. **GeoIP lookup** - Use MaxMind GeoLite2 database for IP geolocation
2. **Webhook notifications** - POST to n8n when email is opened
3. **Browser extension** - Auto-inject pixels when composing in Gmail
4. **Email notifications** - Send yourself an alert on first open
5. **Click tracking** - Wrap links to track clicks (more complex)

---

## Security Considerations

1. **Pixel endpoint must be public** (no auth) - otherwise tracking won't work
2. **Dashboard should be protected** - HTTP Basic Auth or API key
3. **Rate limiting** - Consider adding rate limiting on pixel endpoint to prevent abuse
4. **No PII in URLs** - Only use opaque UUIDs, never email addresses in pixel URLs
5. **HTTPS required** - Mixed content will block pixels in many clients

---

## Commands for Claude Code

**SSH Connection:**
```bash
ssh michael@tachyonfuture.com
```

**Execution order:**

1. SSH into server
2. Set up directory structure and venv
3. Connect to MySQL container and create `mailtrack` database + tables
4. Create and test pixel endpoint alone first (hardcode DB connection if needed)
5. Add full database integration and verify logging works
6. Add dashboard UI
7. Configure systemd service
8. Configure Nginx Proxy Manager (may need to do this via web UI)
9. Final end-to-end test

**Important:** 
- MySQL root credentials are in CLAUDE.md
- Nginx Proxy Manager credentials are in CLAUDE.md
- The pixel endpoint (`/p/{id}.gif`) must remain publicly accessible without authentication
- Dashboard routes should be protected (implement in-app auth or use NPM access lists)

The most critical component is the pixel endpoint - get that working first before building out the dashboard.
