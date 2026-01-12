import argparse
import json
import time
import os
import urllib.parse
import requests
import sqlite3
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from urllib.parse import urljoin, urlparse

# Defaults (max_pages=0 means discover all and download all)
DEFAULT_MAX_PAGES = 0

# These will be set at runtime based on CLI flags or interactive input
BASE_URL = None
output_folder = None
max_pages = DEFAULT_MAX_PAGES


def parse_args():
    parser = argparse.ArgumentParser(description="Playwright website cloner")
    parser.add_argument("--base-url", help="Base URL to start crawling (required or asked interactively)")
    parser.add_argument("--output", help="Output folder for the cloned site (required or asked interactively)")
    parser.add_argument("--cookies", help="Path to cookies JSON file (optional; will ask interactively if omitted)")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Maximum pages to download (0 = all discovered pages)")
    parser.add_argument("--ignore-link", action="append", default=[], help="Substring to ignore links (can repeat, e.g., --ignore-link signout)")
    parser.add_argument("--headless", action="store_true", help="Force headless mode")
    parser.add_argument("--no-headless", dest="headless", action="store_false", help="Disable headless mode (show browser)")
    parser.set_defaults(headless=None)
    return parser.parse_args()


def normalize_base_url(url: str) -> str:
    return url if url.endswith('/') else url + '/'


def ensure_output_dirs(folder: str):
    os.makedirs(folder, exist_ok=True)
    for sub in ["css", "js", "images", "fonts"]:
        os.makedirs(os.path.join(folder, sub), exist_ok=True)

downloaded_resources = {"css": [], "js": [], "images": [], "fonts": []}
visited_pages = set()
pages_to_visit = [BASE_URL]
downloaded_pages = []

def init_db(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pages (
            url TEXT PRIMARY KEY,
            status TEXT CHECK(status IN ('queued','downloaded','error')) NOT NULL,
            file_path TEXT,
            last_attempt TEXT
        )
        """
    )
    conn.commit()
    return conn

def db_mark_queued(conn, url: str):
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO pages(url, status) VALUES (?, 'queued')", (url,))
    conn.commit()

def db_mark_downloaded(conn, url: str, file_path: str):
    cur = conn.cursor()
    cur.execute(
        "UPDATE pages SET status='downloaded', file_path=?, last_attempt=? WHERE url=?",
        (file_path, datetime.utcnow().isoformat(), url,)
    )
    conn.commit()

def db_get_queued(conn):
    cur = conn.cursor()
    cur.execute("SELECT url FROM pages WHERE status='queued'")
    return [row[0] for row in cur.fetchall()]

def db_is_downloaded(conn, url: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pages WHERE url=? AND status='downloaded'", (url,))
    return cur.fetchone() is not None

def get_resource_type(url):
    """Determine resource type from URL"""
    parsed = urlparse(url)
    path = parsed.path.lower()
    
    if path.endswith('.css'): return 'css'
    if path.endswith(('.js', '.json')): return 'js'
    if path.endswith(('.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico')): return 'images'
    if path.endswith(('.woff', '.woff2', '.ttf', '.eot', '.otf')): return 'fonts'
    return None

def download_resource(url, resource_type):
    """Download a resource and return local path"""
    if not resource_type or url.startswith('data:'):
        return url
    
    try:
        # Avoid duplicates
        if url in downloaded_resources[resource_type]:
            return url
        
        print(f"  Downloading {resource_type}: {url}")
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            # Extract filename
            parsed = urlparse(url)
            filename = os.path.basename(parsed.path) or 'index'
            if not filename or '.' not in filename:
                ext_map = {'css': '.css', 'js': '.js', 'images': '.png', 'fonts': '.woff2'}
                filename = f"resource_{len(downloaded_resources[resource_type])}{ext_map.get(resource_type, '')}"
            
            filepath = os.path.join(output_folder, resource_type, filename)
            with open(filepath, 'wb') as f:
                f.write(response.content)
            
            downloaded_resources[resource_type].append(url)
            return os.path.join(resource_type, filename)
    except Exception as e:
        print(f"  Error downloading {url}: {e}")
    
    return url

def rewrite_links(soup, base_url, ignore_patterns=None):
    """Rewrite internal links to point to local files and collect new pages"""
    ignore_patterns = ignore_patterns or []
    modified_count = 0
    new_pages = []
    
    for link in soup.find_all('a', href=True):
        href = link.get('href')
        if not href:
            continue

        href_lower = href.lower()
        if any(p in href_lower for p in ignore_patterns):
            continue
        
        # Skip anchor-only links and javascript
        if href.startswith('#') or href.startswith('javascript:') or href.startswith('mailto:'):
            continue
        
        # Make absolute URL
        full_url = urljoin(base_url, href)
        parsed_url = urlparse(full_url)
        parsed_base = urlparse(base_url)
        
        # If it's an internal link (same domain)
        if parsed_url.netloc == parsed_base.netloc:
            # Get the path and convert to local file reference
            path = parsed_url.path
            query = parsed_url.query
            
            if path == '/' or path == '':
                local_href = 'index.html'
                page_url = BASE_URL
            else:
                # Remove leading slash and add .html if needed
                local_href = path.lstrip('/') or 'index.html'
                if query:
                    local_href = local_href.replace('/', '_') + '.html'
                elif not local_href.endswith(('.html', '.htm', '/')):
                    if local_href.endswith('/'):
                        local_href += 'index.html'
                    else:
                        local_href += '.html'
                
                page_url = urljoin(BASE_URL, path)
            
            link['href'] = local_href
            new_pages.append(page_url)
            modified_count += 1
        else:
            # External link
            link['target'] = '_blank'
    
    return modified_count, new_pages

def discover_all_pages(page, base_url: str, ignore_patterns):
    discovered = set()
    queue = [base_url]
    parsed_base = urlparse(base_url)

    while queue:
        current = queue.pop(0)
        if current in discovered:
            continue
        discovered.add(current)

        try:
            page.goto(current, wait_until='networkidle', timeout=60000)
            time.sleep(0.3)
            html = page.content()
            soup = BeautifulSoup(html, 'html.parser')
            _, new_pages = rewrite_links(soup, current, ignore_patterns)

            for u in new_pages:
                pu = urlparse(u)
                if pu.netloc == parsed_base.netloc and u not in discovered and u not in queue:
                    queue.append(u)
        except Exception:
            # Ignore discovery errors, continue
            continue

    return discovered

def main():
    global BASE_URL, output_folder, max_pages, downloaded_resources, visited_pages, pages_to_visit, downloaded_pages

    args = parse_args()

    # Resolve base URL
    if args.base_url:
        BASE_URL = normalize_base_url(args.base_url)
    else:
        user_url = input("Enter base URL to crawl (e.g., https://example.com/): ").strip()
        if not user_url:
            print("Base URL is required. Exiting.")
            return
        BASE_URL = normalize_base_url(user_url)

    # Resolve output folder
    if args.output:
        output_folder = args.output
    else:
        user_out = input("Enter output folder path: ").strip()
        if not user_out:
            print("Output folder is required. Exiting.")
            return
        output_folder = user_out

    # Resolve cookies path (optional)
    cookies_path = args.cookies
    if cookies_path is None:
        user_cookies = input("Cookies JSON path (press Enter to skip): ").strip()
        cookies_path = user_cookies if user_cookies else None

    # Resolve headless
    if args.headless is None:
        headless_answer = input("Run headless? [Y/n]: ").strip().lower()
        headless = False if headless_answer in ["n", "no"] else True
    else:
        headless = args.headless

    max_pages = args.max_pages
    ignore_patterns = [p.lower() for p in (args.ignore_link or [])]

    downloaded_resources = {"css": [], "js": [], "images": [], "fonts": []}
    visited_pages = set()
    downloaded_pages = []

    db_path = os.path.join(output_folder, 'clone_state.db')
    conn = init_db(db_path)

    queued_from_db = db_get_queued(conn)
    if queued_from_db:
        pages_to_visit = queued_from_db.copy()
    else:
        pages_to_visit = [BASE_URL]
        db_mark_queued(conn, BASE_URL)

    ensure_output_dirs(output_folder)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()

        # Load cookies if provided
        if cookies_path and os.path.exists(cookies_path):
            print("Loading cookies...")
            try:
                with open(cookies_path, 'r') as f:
                    cookies = json.load(f)
                    for cookie in cookies:
                        if 'sameSite' in cookie and cookie['sameSite'] not in ['Strict', 'Lax', 'None']:
                            cookie['sameSite'] = 'Lax'
                    context.add_cookies(cookies)
            except Exception as e:
                print(f"  Warning: failed to load cookies: {e}")
        else:
            print("No cookies file provided; crawling unauthenticated")

        page = context.new_page()

        print("\n" + "="*60)
        print("DISCOVERY PHASE")
        print("="*60)

        if max_pages == 0:
            discovered = discover_all_pages(page, BASE_URL, ignore_patterns)
            for url in discovered:
                db_mark_queued(conn, url)
            pages_to_visit = db_get_queued(conn)
            print(f"Discovered pages: {len(pages_to_visit)}")

        print("\n" + "="*60)
        print("CRAWLING WEBSITE PAGES")
        print("="*60)

        page_count = 0
        timeout_count = 0

        while pages_to_visit and (max_pages == 0 or page_count < max_pages):
            page_url = pages_to_visit.pop(0)

            if page_url in visited_pages or db_is_downloaded(conn, page_url):
                continue

            visited_pages.add(page_url)
            page_count += 1

            print(f"\n[{page_count}] Downloading: {page_url}")

            try:
                page.goto(page_url, wait_until='networkidle', timeout=60000)
                time.sleep(0.5)

                html_content = page.content()
                soup = BeautifulSoup(html_content, 'html.parser')

                for link in soup.find_all('link', rel='stylesheet'):
                    href = link.get('href')
                    if href:
                        full_url = urljoin(BASE_URL, href)
                        local_path = download_resource(full_url, 'css')
                        link['href'] = local_path

                for script in soup.find_all('script', src=True):
                    src = script.get('src')
                    if src:
                        full_url = urljoin(BASE_URL, src)
                        local_path = download_resource(full_url, 'js')
                        script['src'] = local_path

                for img in soup.find_all('img'):
                    src = img.get('src')
                    if src:
                        full_url = urljoin(BASE_URL, src)
                        local_path = download_resource(full_url, 'images')
                        img['src'] = local_path

                _, new_pages = rewrite_links(soup, page_url, ignore_patterns)

                for new_page in new_pages:
                    if new_page not in visited_pages and new_page not in pages_to_visit:
                        pages_to_visit.append(new_page)

                parsed_url = urlparse(page_url)
                path = parsed_url.path
                query = parsed_url.query

                if path == '/' or path == '':
                    file_path = os.path.join(output_folder, 'index.html')
                else:
                    local_path = path.lstrip('/') or 'index.html'
                    if query:
                        local_path = local_path.replace('/', '_') + '.html'
                    elif not local_path.endswith(('.html', '.htm')):
                        if local_path.endswith('/'):
                            local_path += 'index.html'
                        else:
                            local_path += '.html'

                    file_path = os.path.join(output_folder, local_path)

                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(str(soup.prettify()))

                downloaded_pages.append(page_url)
                print(f"  ✓ Saved: {file_path}")
                db_mark_downloaded(conn, page_url, file_path)

            except Exception as e:
                timeout_count += 1
                print(f"  ✗ Error: {e}")
                if timeout_count > 5:
                    print("  (Skipping page due to repeated timeouts)")

        browser.close()
        conn.close()

    print("\n" + "="*60)
    print("✓ WEBSITE CRAWL COMPLETE!")
    print("="*60)
    print(f"Location: {output_folder}")
    print(f"\nPages downloaded: {len(downloaded_pages)}")
    print(f"Resources downloaded:")
    print(f"  CSS files: {len(downloaded_resources['css'])}")
    print(f"  JS files: {len(downloaded_resources['js'])}")
    print(f"  Images: {len(downloaded_resources['images'])}")
    print(f"  Fonts: {len(downloaded_resources['fonts'])}")
    print(f"\nOpen {os.path.join(output_folder, 'index.html')} to view the cloned website")
    print("="*60)


if __name__ == "__main__":
    main()