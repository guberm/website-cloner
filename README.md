# Website Clone Crawler

A configurable Playwright-based crawler to mirror websites locally with CSS, JavaScript, images, and links rewritten to work offline.

## Features
- Crawls websites with configurable depth limit
- Downloads and organizes all assets (CSS, JS, images, fonts)
- Rewrites internal links to point to local HTML files
- Optional authentication via cookies
- Filters out unwanted links (logout, signout, etc.)
- Headless or visible browser mode
- Interactive or CLI-based configuration

## Installation

### 1. Install Dependencies
Ensure Python 3.8+ is installed, then:
```bash
pip install playwright beautifulsoup4 requests
playwright install chromium
```

### 2. Run the Script
Interactive mode (prompts for inputs):
```bash
python "website clone.py"
```

Or with CLI flags (non-interactive):
```bash
python "website clone.py" \
  --base-url "https://example.com/" \
  --output "./cloned-site" \
  --max-pages 100 \
  --ignore-link logout \
  --ignore-link signout
```

## CLI Options

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--base-url` | URL | Yes* | Website root to start crawling |
| `--output` | Path | Yes* | Destination folder for cloned site |
| `--cookies` | Path | No | JSON file with browser cookies for authentication |
| `--max-pages` | Integer | No | Max pages to crawl (default: 500) |
| `--ignore-link` | String | No | Link substrings to skip (repeatable; e.g., `--ignore-link logout --ignore-link signout`) |
| `--headless` | Flag | No | Run browser in headless mode |
| `--no-headless` | Flag | No | Run browser with visible UI |

*Required via CLI flag or interactive prompt if omitted.

## How It Works

1. **Crawling**: Uses Playwright to fetch pages with full JavaScript rendering
2. **Asset Download**: Downloads CSS, JS, images, and fonts into organized subfolders
3. **Link Rewriting**: Converts absolute URLs to relative paths (`/page` → `page.html`)
4. **Local Navigation**: All links point to local files for offline browsing
5. **Breadth-First**: Traverses links until `--max-pages` limit is reached

## Output Structure
```
cloned-site/
├── index.html
├── css/
│   └── *.css
├── js/
│   └── *.js
├── images/
│   └── *.(png|jpg|svg|etc)
├── fonts/
│   └── *(woff|ttf|etc)
└── [other-pages]/
    └── *.html
```

## Examples

### Clone with Authentication
```bash
python "website clone.py" \
  --base-url "https://mysite.com/" \
  --output "./my-site-backup" \
  --cookies "./cookies.json" \
  --max-pages 200
```

### Clone and Skip Logout Links
```bash
python "website clone.py" \
  --base-url "https://myapp.com/" \
  --output "./app-clone" \
  --ignore-link /logout \
  --ignore-link /signout \
  --ignore-link /account/delete
```

### Interactive Mode
```bash
python "website clone.py"
# Then follow prompts to enter base URL, output folder, etc.
```

## Notes
- Pages that rely on real-time backend data may show placeholders offline
- Large websites (100+ pages) with heavy resources may take time to download
- Some sites may block automated crawling; use `--cookies` for authenticated sessions
- External links open in new tabs (target="_blank") and are not crawled

## License
MIT
