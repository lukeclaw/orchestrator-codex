# Screenshot & GitHub Upload

Capture screenshots via Playwright MCP and host them for use in PR descriptions or comments.

## When to Use
- UI changes that benefit from visual documentation
- Before/after comparisons
- Bug reproduction evidence

---

## Procedure

### Step 1: Ensure Chromium is installed

```bash
npx playwright install chromium
```

If Chrome is not found at `/opt/google/chrome/chrome`, create the symlink:
```bash
sudo mkdir -p /opt/google/chrome
sudo ln -sf ~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome /opt/google/chrome/chrome
```

### Step 2: Capture screenshot via Playwright MCP

Use the Playwright MCP tools:

1. **Navigate** to the target URL:
   - Use `browser_navigate` with the URL you want to capture

2. **Take screenshot**:
   - Use `browser_take_screenshot` to save the image
   - Save to a descriptive filename (e.g., `feature-before.png`, `dashboard-new-ui.png`)

### Step 3: Host via GitHub draft release

GitHub draft releases provide stable URLs that don't expire.

```bash
# Create draft release (one-time per repo, can reuse)
gh release create screenshot-assets --draft --title "Screenshots" --notes "Screenshot hosting for PRs"

# Upload screenshot
gh release upload screenshot-assets screenshot.png --clobber

# Get the stable URL
gh release view screenshot-assets --json assets --jq '.assets[] | select(.name=="screenshot.png") | .url'
```

### Step 4: Use in PR

Insert the URL in your PR description or comment:

```markdown
## Screenshots

![Feature Screenshot](https://github.com/OWNER/REPO/releases/download/screenshot-assets/screenshot.png)
```

---

## Important Notes

- **Avoid `raw.githubusercontent.com` URLs** — These include temporary tokens that expire
- **Draft releases are stable** — The URL won't change and doesn't require authentication to view
- **Reuse the draft release** — Upload multiple screenshots to the same release with `--clobber` to overwrite
- **Descriptive filenames** — Use meaningful names since they appear in the URL
