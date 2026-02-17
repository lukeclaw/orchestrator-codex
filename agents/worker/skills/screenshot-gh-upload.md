---
name: screenshot-gh-upload
description: Capture screenshots via Playwright MCP and upload to GitHub for PR descriptions.
---

# Screenshot & GitHub Upload

## Step 1: Capture screenshot

Use Playwright MCP: `browser_navigate` → `browser_take_screenshot`. If Chromium is missing:
```bash
npx playwright install chromium
```

## Step 2: Upload via GitHub Contents API

Uses a **dedicated screenshots repo** (e.g., `OWNER/screenshots`) to avoid bloating the working repo. The Contents API (`PUT /repos/{owner}/{repo}/contents/{path}`) is more reliable than release assets which often 404.

```bash
SCREENSHOT_REPO="linkedin-sandbox/yuqiu-screenshots"
FILENAME="screenshot.png"
IMG_PATH="$(date +%Y-%m)/${FILENAME}"  # organize by month

# Get existing file SHA if overwriting
EXISTING_SHA=$(gh api "repos/${SCREENSHOT_REPO}/contents/${IMG_PATH}" -q .sha 2>/dev/null || echo "")
SHA_ARG=""
[ -n "${EXISTING_SHA}" ] && SHA_ARG="-f sha=${EXISTING_SHA}"

# Upload
gh api --method PUT "repos/${SCREENSHOT_REPO}/contents/${IMG_PATH}" \
  -f message="screenshot ${FILENAME}" \
  -f content="$(base64 < "${FILENAME}")" \
  ${SHA_ARG}
```

## Step 3: Use in PR

Use the `html_url` from the API response with `?raw=true` appended:
```markdown
![Screenshot](https://github.com/linkedin-sandbox/yuqiu-screenshots/blob/main/2026-02/screenshot.png?raw=true)
```
