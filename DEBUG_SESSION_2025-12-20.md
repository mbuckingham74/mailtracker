# Mailtracker Debug Session - December 20-21, 2025

## Problem Summary

Two main issues were worked on:

### 1. Tracking Pixel Not Surviving Gmail Send (RESOLVED ✅)
When emails are sent via Gmail with the Chrome extension, the tracking pixel was:
- Created successfully in the database
- Inserted into the compose body (verified in DOM before send)
- **STRIPPED by Gmail during the send process**

**Solution**: XHR interception at the network layer (see "Final Solution" section below).

### 2. Recipient Extraction Picking Up Suggestions (RESOLVED ✅)
The extension was capturing contact suggestions from Gmail's autocomplete dropdown instead of just actual recipients in To/CC/BCC fields.

**Solution**: Filter out elements inside `[role="listbox"]` (suggestion dropdown).

---

## What We Tried

### Recipient Extraction Fixes

#### Attempt 1: Scope to aria-label containers
- Look for `[aria-label*="To"]`, `[aria-label*="Cc"]`, `[aria-label*="Bcc"]` containers
- Then find `[email]` elements within those containers
- **Result**: Still picked up suggestions

#### Attempt 2: Use input[name] to find specific inputs
- Find `input[name="to"]`, `input[name="cc"]`, `input[name="bcc"]`
- Look for chips in the same row/container
- **Result**: Failed completely - showed "Unknown" for all recipients

#### Attempt 3: Filter out listbox elements (CURRENT)
- Find ALL `[email]` elements in compose window
- Exclude those inside `[role="listbox"]` (suggestion dropdown)
- Also tried excluding `[role="presentation"]` unless in `[role="option"]`
- Added chip detection (data-name, .afV class, tabindex="0")
- **Result**: Test #16 and #17 both showed "Unknown"

#### Attempt 4: Simplified - only exclude listbox (LATEST)
- Find ALL `[email]` elements
- ONLY exclude if inside `[role="listbox"]`
- Accept everything else
- Added detailed debug logging
- **Status**: Pushed but not yet tested

### Key Code Location
`/Users/michaelbuckingham/Documents/my-apps/mailtracker-extension/content/gmail.js`

Current `extractRecipients` function (lines 68-105):
```javascript
function extractRecipients(composeWindow) {
  const recipients = new Set();

  const allEmailElements = composeWindow.querySelectorAll('[email]');
  console.log('Mailtrack: Found', allEmailElements.length, 'elements with [email] attribute');

  allEmailElements.forEach(el => {
    const email = el.getAttribute('email');
    if (!email || !email.includes('@')) return;

    // Log element details for debugging
    console.log('Mailtrack: Checking email:', email);
    console.log('  - tagName:', el.tagName);
    console.log('  - className:', el.className);
    console.log('  - has data-name:', el.hasAttribute('data-name'));
    console.log('  - in listbox:', !!el.closest('[role="listbox"]'));

    // ONLY exclude if inside a listbox (suggestions dropdown)
    if (el.closest('[role="listbox"]')) {
      console.log('Mailtrack: Skipping (in suggestion listbox):', email);
      return;
    }

    // Accept this email
    console.log('Mailtrack: Accepting recipient:', email);
    recipients.add(email);
  });

  const result = [...recipients];
  console.log('Mailtrack: Total recipients found:', result);
  return result;
}
```

---

## Pixel Insertion Strategy

The extension intercepts the Send button click:
1. On mousedown, prevent default
2. Insert tracking pixel via `document.execCommand('insertHTML')`
3. Wait 300ms for DOM to settle
4. Dispatch click event to actually send

### Pixel Formats Tried (in order)
1. `<img src="${url}">`
2. `<img src="${url}" width="1" height="1">`
3. `<div><img src="${url}" width="1" height="1"></div>`
4. Direct DOM: `document.createElement('img')` + `appendChild`

### Key Insight
Gmail strips programmatically-inserted content during send. The hypothesis was that `execCommand('insertHTML')` makes content look "user-typed" and survives sanitization - but this isn't working.

---

## Dashboard Display Fixes (RESOLVED)

### Issue: Only showing first recipient
The template was doing `recipient.split('@')[0]` on the entire comma-separated string.

### Fix
Split by comma first, then iterate:
```jinja2
{% for email in item.track.recipient.split(',') %}
<span class="recipient-pill">{{ email.strip().split('@')[0] }}</span>
{% endfor %}
```

Applied to both grouped and ungrouped track display in `dashboard.html`.

---

## Server-Side Notes

### Open Filtering
`pixel.py` has a 5-second delay filter (`MIN_OPEN_DELAY_SECONDS = 5`) to ignore opens that happen immediately after track creation (which would be the sender's browser loading the pixel).

### Proxy Detection
Dashboard filters out Apple Mail Privacy Protection and Google Image Proxy opens from the "real opens" count.

---

## Test Results

| Test # | Recipients Shown | Opens | Notes |
|--------|-----------------|-------|-------|
| #9 | pamncharlie (wrong) | 0 | Suggestion was captured |
| #14 | Unknown | 0 | |
| #15 | Unknown | 0 | |
| #16 | Unknown | 0 | input[name] approach failed |
| #17 | Unknown | 0 | Chip detection too restrictive |
| #18 | ? | ? | Pending - simplified listbox filter |

---

## Final Solution: XHR Interception (December 21, 2025)

### Why DOM Manipulation Failed
All DOM-based approaches (execCommand, insertHTML, appendChild, innerHTML) failed because Gmail sanitizes programmatically-inserted content during the send process. The pixel would appear in the DOM but get stripped before the email was sent.

### The Working Approach: XHR Interception

Instead of modifying the DOM, we intercept Gmail's XHR request at the network layer and inject the pixel into the request body AFTER Gmail has processed the email but BEFORE it leaves the browser.

### Implementation

**New file: `content/xhr-interceptor.js`**
- Injected into page context (not content script) to access XHR
- Overrides `XMLHttpRequest.prototype.send` and `window.fetch`
- Waits for pixel data from content script
- Injects pixel into JSON-escaped HTML in request body

**Updated: `content/gmail.js`**
- Injects xhr-interceptor.js into page context on load
- On send button mousedown: signals interceptor to wait, creates pixel via API
- Sends pixel URL to interceptor via custom event

**Updated: `manifest.json`**
- Added `xhr-interceptor.js` to `web_accessible_resources`
- Changed `run_at` to `document_start`

### Key Technical Details

1. **Timing Synchronization**: Gmail's XHR fires almost instantly after click. Solution:
   - Mousedown event dispatches `mailtrack-prepare-send` signal synchronously
   - XHR interceptor delays send up to 2 seconds waiting for pixel
   - Content script creates pixel asynchronously, signals when ready

2. **JSON Escaping**: Gmail's request body contains JSON-escaped HTML like:
   ```
   "<div dir=\"ltr\">content</div>"
   ```
   Pixel must be escaped the same way:
   ```javascript
   pixelHtml.replace(/"/g, '\\"')
   ```

3. **Injection Pattern**: Find `</div>(?=")` and insert escaped pixel before it

### Test Results

| Test # | Result | Notes |
|--------|--------|-------|
| #21-22 | Failed | Timing race - XHR fired before pixel ready |
| #23 | Failed | Debugging - captured Gmail's request body format |
| #24 | Failed | 400 Bad Request - JSON escaping issue |
| #25 | **SUCCESS** | Pixel in email, real opens tracked |

### Verification

Test #25 was opened on iPhone Mail.app and recorded:
- Real open from Miami IP (172.226.188.x)
- Google Image Proxy opens filtered correctly (proxy detection working)
- 5-second delay filter working (sender's browser load ignored)

---

## File Locations

### Extension (Chrome)
- `/Users/michaelbuckingham/Documents/my-apps/mailtracker-extension/`
- Main file: `content/gmail.js`
- Background: `background/service-worker.js`

### Backend (Server)
- `/Users/michaelbuckingham/Documents/my-apps/mailtracker/`
- Server path: `/opt/mailtrack` on tachyonfuture.com
- Dashboard: `app/routes/dashboard.py`
- Pixel endpoint: `app/routes/pixel.py`
- Templates: `app/templates/`

### Credentials
- Dashboard: michael / REDACTED_PASSWORD
- API Key: `REDACTED_API_KEY`
- Server: michael@tachyonfuture.com (sudo: REDACTED_PASSWORD)

---

## Commands Reference

```bash
# Reload extension after changes
# Go to chrome://extensions/ and click refresh on Mailtrack

# Deploy backend changes
ssh michael@tachyonfuture.com
cd /opt/mailtrack
git pull
docker compose up -d --build

# Check logs
docker logs -f mailtrack

# Test API
curl https://mailtrack.tachyonfuture.com/health
```
