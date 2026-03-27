<p align="center">
  <img src="images/mailtrack_transparent_logo.png" alt="Mailtrack" width="300">
</p>

<h1 align="center">Mailtrack</h1>

<p align="center">
  <strong>Self-hosted email tracking for Gmail</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/FastAPI-0.109-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/MySQL-8.0-4479A1?style=flat-square&logo=mysql&logoColor=white" alt="MySQL 8.0">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/Chrome-Extension-4285F4?style=flat-square&logo=googlechrome&logoColor=white" alt="Chrome Extension">
</p>

<p align="center">
  Track when your emails are opened with invisible 1x1 tracking pixels.<br>
  Similar to Mailsuite/Mailtrack.io, but self-hosted and privacy-respecting.
</p>

---

## Features

### Dashboard
- **Real-time tracking** - See when emails are opened with location data
- **Pinned emails** - Star important emails to keep them at the top
- **Notes** - Add context to tracked emails (e.g., "Discussed pricing")
- **Smart filtering** - Filter by opened/unopened, date range, or search
- **Pagination** - Handle large email volumes efficiently

### Analytics
- **Open rate tracking** - Monitor your email engagement over time
- **Geographic insights** - See where your emails are being opened
- **Time analysis** - Discover the best times to send emails
- **Export to CSV** - Download analytics data for further analysis

### Recipients
- **Engagement scoring** - 0-100 score based on open behavior
- **Per-recipient history** - Full email history with each contact
- **Sortable metrics** - Sort by emails sent, open rate, or engagement score

### Notifications
- **Email alerts** - Get notified when emails are opened
- **Follow-up reminders** - Automatic reminder if email unopened after 3 days
- **Browser notifications** - Desktop alerts via Chrome extension

### Privacy & Intelligence
- **Proxy detection** - Distinguish real opens from Apple/Google privacy proxies
- **Self-hosted** - Your data stays on your server
- **Dark mode** - Easy on the eyes

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Your Browser                             │
│  ┌─────────────────┐              ┌─────────────────────────┐   │
│  │ Chrome Extension │◄────────────►│        Gmail            │   │
│  │ (Auto-injects    │              │                         │   │
│  │  tracking pixel) │              │   Compose & Send Email  │   │
│  └────────┬─────────┘              └─────────────────────────┘   │
└───────────┼──────────────────────────────────────────────────────┘
            │
            │ API calls
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Your Server                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    FastAPI Backend                       │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐  │    │
│  │  │ Tracking │  │ Dashboard│  │ Analytics│  │   API   │  │    │
│  │  │  Pixel   │  │   UI     │  │  Charts  │  │Endpoints│  │    │
│  │  └──────────┘  └──────────┘  └──────────┘  └─────────┘  │    │
│  └─────────────────────────┬───────────────────────────────┘    │
│                            │                                     │
│  ┌─────────────────────────▼───────────────────────────────┐    │
│  │                      MySQL 8.0                           │    │
│  │           tracked_emails  │  opens                       │    │
│  └──────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
            ▲
            │ Tracking pixel request
            │
┌───────────┼──────────────────────────────────────────────────────┐
│           │              Recipient's Email Client                │
│  ┌────────┴─────────┐                                            │
│  │   Opens Email    │  ──► Loads 1x1 tracking pixel              │
│  │   (Gmail, etc.)  │  ──► Server logs open with IP/location     │
│  └──────────────────┘                                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Screenshots

### Dashboard
View all tracked emails with open counts, delivery status, and notes.

### Analytics
Charts showing opens over time, by hour, and by day of week.

### Recipients
Engagement scores help identify your most engaged contacts.

---

## Quick Start

### Prerequisites
- Docker & Docker Compose
- A domain with SSL (for production)
- Gmail account with App Password (for notifications)

### 1. Clone the repository

```bash
git clone https://github.com/mbuckingham74/mailtracker.git
cd mailtracker
```

### 2. Configure environment

```bash
cp .env.example .env
nano .env
```

Required environment variables:

```bash
# Database
DATABASE_URL=mysql+aiomysql://root:your-password@mailtrack-mysql:3306/mailtrack

# Security
SECRET_KEY=your-secret-key-here
API_KEY=your-api-key-here
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=your-password-here
TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16

# Notifications (optional)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-gmail-app-password
NOTIFICATION_EMAIL=your-email@gmail.com
FOLLOWUP_DAYS=3
```

### 3. Start the server

```bash
docker compose up -d
```

On startup the app will create missing tables and backfill any known compatibility columns required by the current ORM schema.
Set `TRUSTED_PROXY_CIDRS` to the address range used by your reverse proxy so forwarded client IPs are only accepted from trusted hops.

### 4. Install Chrome Extension

1. Clone the extension repo: `git clone https://github.com/mbuckingham74/mailtracker-extension.git`
2. Go to `chrome://extensions/`
3. Enable "Developer mode"
4. Click "Load unpacked" and select the extension folder
5. Click the extension icon and enter your API key

---

## API Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/p/{id}.gif` | GET | None | Tracking pixel |
| `/api/tracks` | GET/POST | API Key | List/create tracks |
| `/api/tracks/{id}` | GET | API Key | Get track details |
| `/api/opens/recent` | GET | API Key | Recent opens |
| `/api/stats` | GET | API Key | Statistics |
| `/health` | GET | None | Health check |

---

## Database Schema

```sql
CREATE TABLE tracked_emails (
    id VARCHAR(36) PRIMARY KEY,
    recipient VARCHAR(255),
    subject VARCHAR(500),
    notes TEXT,
    message_group_id VARCHAR(36),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    notified_at DATETIME NULL,
    pinned BOOLEAN DEFAULT FALSE,
    followup_notified_at DATETIME NULL,
    hot_notified_at DATETIME NULL,
    revived_notified_at DATETIME NULL
);

CREATE TABLE opens (
    id INT AUTO_INCREMENT PRIMARY KEY,
    tracked_email_id VARCHAR(36),
    opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    ip_address VARCHAR(45),
    user_agent TEXT,
    referer TEXT,
    country VARCHAR(100),
    city VARCHAR(100),
    FOREIGN KEY (tracked_email_id) REFERENCES tracked_emails(id) ON DELETE CASCADE
);
```

---

## Privacy Features

### Proxy Detection
The system automatically detects and labels opens from email privacy proxies:
- **Apple Mail Privacy Protection** - IPs in 17.0.0.0/8 range
- **Google Image Proxy** - Various Google IP ranges

This helps distinguish real opens from automated prefetches.

### Self-Hosted
Unlike commercial solutions:
- Your tracking data stays on your server
- No third-party has access to your email metadata
- Full control over data retention

---

## Tech Stack

- **Backend**: FastAPI (Python 3.12)
- **Database**: MySQL 8.0 with SQLAlchemy + aiomysql
- **GeoIP**: MaxMind GeoLite2-City (auto-downloaded)
- **Frontend**: Jinja2 templates + Chart.js
- **Extension**: Chrome Manifest V3
- **Deployment**: Docker Compose

---

## License

MIT License - feel free to self-host and modify.

---

## Related Repositories

- [mailtracker-extension](https://github.com/mbuckingham74/mailtracker-extension) - Chrome extension for Gmail
