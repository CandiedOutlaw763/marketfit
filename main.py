import os
import json
import asyncio
import logging
import random
import socket
import ipaddress
from urllib.parse import urlparse
import requests
import time
from flask import Flask, request, jsonify, render_template
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from groq import Groq
from curl_cffi.requests import AsyncSession
import feedparser
from google_play_scraper import Sort, reviews as play_reviews, search as play_search, app as play_app
import re

# --- USER AGENTS POOL ---
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Android 14; Mobile; rv:121.0) Gecko/121.0 Firefox/121.0",
]

# --- CONFIGURATION & LOGGING ---
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Security: Load Key from Env
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    logger.warning("GROQ_API_KEY not set! AI features will fail.")

client = Groq(api_key=GROQ_API_KEY)
MODEL = "llama-3.3-70b-versatile"  # Using 8b for cost efficiency

# Production: Rate Limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["10 per minute"],
    storage_uri="memory://"
)

# --- 1. SECURITY MANAGER (SSRF & VALIDATION) ---
class SecurityManager:
    ALLOWED_DOMAINS = [
        "hacker-news.firebaseio.com",
        "reddit.com", "www.reddit.com", "old.reddit.com",
        "itunes.apple.com", "apps.apple.com"
    ]

    @staticmethod
    def is_safe_url(url):
        """
        Check 1: Scheme (HTTPS only)
        Check 2: Hostname Allowlist
        Check 3: DNS Resolution (No Private IPs)
        """
        try:
            parsed = urlparse(url)
            
            # 1. Protocol Restriction
            if parsed.scheme != "https":
                logger.warning(f"Blocked non-HTTPS URL: {url}")
                return False

            hostname = parsed.hostname.lower()

            # 2. Strict Allowlist (Exact Match or Subdomain)
            is_allowed = False
            for allowed in SecurityManager.ALLOWED_DOMAINS:
                if hostname == allowed or hostname.endswith("." + allowed):
                    is_allowed = True
                    break
            
            if not is_allowed:
                logger.warning(f"Blocked unauthorized domain: {hostname}")
                return False

            # 3. DNS Resolution (SSRF Protection)
            # Resolve hostname to IP to ensure it's not local (127.0.0.1, 192.168.x.x)
            ip_list = socket.gethostbyname_ex(hostname)[2]
            for ip in ip_list:
                ip_obj = ipaddress.ip_address(ip)
                if ip_obj.is_private or ip_obj.is_loopback:
                    logger.warning(f"Blocked private IP resolution: {ip} for {hostname}")
                    return False

            return True

        except Exception as e:
            logger.error(f"URL Validation Failed: {e}")
            return False

# --- 2. SCRAPING ENGINES (STEALTH & RELIABILITY) ---

# ENGINE A: HACKER NEWS (API)
async def scrape_hn_opportunities(limit=20):
    url = "https://hacker-news.firebaseio.com/v0/askstories.json"
    if not SecurityManager.is_safe_url(url): return []

    async with AsyncSession(impersonate="chrome110", timeout=10) as session:
        try:
            # Random Jitter
            await asyncio.sleep(random.uniform(0.5, 1.5))
            
            resp = await session.get(url)
            story_ids = resp.json()[:limit]
            
            tasks = []
            for sid in story_ids:
                item_url = f"https://hacker-news.firebaseio.com/v0/item/{sid}.json"
                if SecurityManager.is_safe_url(item_url):
                    tasks.append(session.get(item_url))
            
            # Fetch details concurrently
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            
            opportunities = []
            keywords = ["how to", "alternative", "wish", "sucks", "problem", "hard to"]
            
            for r in responses:
                if isinstance(r, Exception) or r.status_code != 200: continue
                story = r.json()
                text = story.get('text', '') or ''
                title = story.get('title', '') or ''
                
                if any(k in title.lower() or k in text.lower() for k in keywords):
                    # Store as object with metadata
                    opportunities.append({
                        "id": str(story.get('id')),
                        "text": f"[HN] {title} - {text[:200]}",
                        "url": f"https://news.ycombinator.com/item?id={story.get('id')}",
                        "source": "Hacker News"
                    })
            
            return opportunities
        except Exception as e:
            logger.error(f"HN Scrape Failed: {e}")
            return []

# ENGINE B: REDDIT (RSS + STEALTH HEADERS)
def scrape_reddit_rss(subreddits):
    opportunities = []
    
    for sub in subreddits:
        # Use 'hot' json feed for better reliability
        clean_sub = sub.strip()
        json_url = f"https://www.reddit.com/r/{clean_sub}/hot.json?limit=25"
        
        if not SecurityManager.is_safe_url(json_url): 
            logger.warning(f"Skipping unsafe URL for {clean_sub}")
            continue
        
        success = False
        attempts = 3
        
        for attempt in range(1, attempts + 1):
            chosen_ua = random.choice(USER_AGENTS)
            headers = {'User-Agent': chosen_ua}
            
            try:
                logger.info(f"Fetching {json_url} (Attempt {attempt}/{attempts})...")
                resp = requests.get(json_url, headers=headers, timeout=10)
                logger.info(f"Reddit [{clean_sub}] Status: {resp.status_code}")
                
                if resp.status_code == 200:
                    data = resp.json()
                    posts = data.get('data', {}).get('children', [])
                    logger.info(f"Reddit [{clean_sub}] Posts found: {len(posts)}")
                    
                    for post in posts:
                        p_data = post.get('data', {})
                        title = p_data.get('title', '')
                        selftext = p_data.get('selftext', '')
                        
                        # Skip stickied posts or very short ones
                        if p_data.get('stickied') or len(title) < 10: continue

                        opportunities.append({
                            "id": p_data.get('id'),
                            "text": f"[Reddit r/{clean_sub}] {title} - {selftext[:200]}",
                            "url": f"https://reddit.com{p_data.get('permalink')}",
                            "source": f"Reddit r/{clean_sub}"
                        })
                    success = True
                    break # Break retry loop on success
                elif resp.status_code == 429:
                    logger.warning(f"Reddit 429 Rate Limit on r/{clean_sub}")
                else: 
                     logger.warning(f"Reddit Failed r/{clean_sub} Code: {resp.status_code}")
            
            except Exception as e:
                logger.error(f"Reddit Request Failed for {clean_sub}: {e}")
            
            if attempt < attempts:
                wait_time = random.uniform(1, 5)
                logger.info(f"Retrying in {wait_time:.2f} seconds...")
                time.sleep(wait_time)
        
        if not success:
            logger.error(f"Failed to scrape r/{clean_sub} after {attempts} attempts.")

    return opportunities

# ENGINE C: APP REVIEWS (PLAY STORE & APPLE)
def search_google_play_manual(query):
    try:
        url = f"https://play.google.com/store/search?q={query}&c=apps"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            # Regex to find the first App ID
            # Matches: href="/store/apps/details?id=com.example.app"
            match = re.search(r'details\?id=([a-zA-Z0-9_.]+)', resp.text)
            if match:
                return match.group(1)
    except Exception as e:
        logger.error(f"Manual Search Error: {e}")
    return None

def find_app(name, platform_preference="android"):
    """
    Search for app in Google Play or iOS App Store based on preference.
    platform_preference: 'android', 'ios'
    Returns dict with platform, id, title or None.
    """
    
    # helper for android search
    def try_android():
        try:
             # We skip the broken libraries search and go straight to manual
            gp_id = search_google_play_manual(name)
            
            if gp_id:
                logger.info(f"Found Android App ID: {gp_id}")
                # Verify and get title
                try:
                    app_details = play_app(gp_id)
                    title = app_details.get("title", name)
                except:
                    title = name 

                return {
                    "platform": "android",
                    "id": gp_id,
                    "title": title,
                    "url": f"https://play.google.com/store/apps/details?id={gp_id}"
                }
        except Exception as e:
            logger.error(f"Google Play Lookup Error: {e}")
        return None

    # helper for ios search
    def try_ios():
        try:
            url = "https://itunes.apple.com/search"
            params = {"term": name, "entity": "software", "limit": 1}
            resp = requests.get(url, params=params, timeout=5)
            data = resp.json()
            if data["resultCount"] > 0:
                first = data["results"][0]
                return {
                    "platform": "ios",
                    "id": first["trackId"],
                    "title": first["trackName"],
                    "url": first["trackViewUrl"]
                }
        except Exception as e:
            logger.error(f"iOS Search Error: {e}")
        return None

    # LOGIC ROUTING
    if platform_preference == "ios":
        return try_ios()
    
    # Default is Android
    return try_android()

def scrape_reviews(target_app_name, platform_preference="android"):
    app_info = find_app(target_app_name, platform_preference)
    if not app_info:
        logger.warning(f"App not found: {target_app_name}")
        return None # Signal 404

    opportunities = []
    
    try:
        if app_info["platform"] == "android":
            logger.info(f"Fetching Android reviews for {app_info['title']} ({app_info['id']})")
            result, _ = play_reviews(
                app_info["id"],
                lang='en',
                country='us',
                sort=Sort.NEWEST,
                count=40
            )
            
            for r in result:
                content = r.get('content', '')
                if len(content) > 10: # Filter short noise
                    opportunities.append({
                        "id": r.get('reviewId'),
                        "text": f"[Android Review] {content[:300]}",
                        "url": app_info["url"],
                        "source": f"Google Play ({app_info['title']})"
                    })
                    
        elif app_info["platform"] == "ios":
             logger.info(f"Fetching iOS reviews for {app_info['title']} ({app_info['id']})")
             feed_url = f"https://itunes.apple.com/us/rss/customerreviews/id={app_info['id']}/sortBy=mostRecent/json"
             resp = requests.get(feed_url, timeout=10)
             data = resp.json()
             entries = data.get('feed', {}).get('entry', [])
             
             for entry in entries:
                 # Skip the first entry if it's the app metadata (sometimes happens in RSS)
                 if 'author' not in entry: continue
                 
                 title = entry.get('title', {}).get('label', '')
                 content = entry.get('content', {}).get('label', '')
                 rating = entry.get('im:rating', {}).get('label', '?')
                 
                 full_text = f"{title} - {content}"
                 if len(full_text) > 10:
                     opportunities.append({
                        "id": entry.get('id', {}).get('label'),
                         "text": f"[iOS Review {rating}/5] {full_text[:300]}",
                         "url": app_info["url"],
                         "source": f"App Store ({app_info['title']})"
                     })

    except Exception as e:
        logger.error(f"Review Scraping Failed: {e}")

    return opportunities

# --- 3. INTELLIGENCE LAYER (AI ANALYSIS) ---
def generate_business_ideas(collected_data):
    if not collected_data: return []
    
    # Prepare data for LLM, including IDs
    # We limit to 30 items to avoid context window issues
    input_data = []
    for i, item in enumerate(collected_data[:30]):
        # We use a simple index-based ID for valid JSON mapping if the original ID is complex
        input_data.append(f"ID: {i} | Content: {item['text']}")
    
    data_str = "\n".join(input_data)
    
    prompt = """
    Analyze these complaints/discussions provided below.
    Output 5 Specific Micro-SaaS Ideas that solve these problems.
    
    Return the response as a valid JSON object with a key "ideas" containing a list of objects.
    Each object in the list must have:
    - "name": Brief name of the idea.
    - "pitch": One sentence pitch.
    - "source_id": The exact integer ID (from the input "ID: X") of the data point that inspired this idea.
    
    Do not add any markdown formatting (like ```json). Just the raw JSON string.
    """
    
    try:
        completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a Product Manager. You output JSON only."},
                {"role": "user", "content": f"{prompt}\nDATA:\n{data_str}"}
            ],
            model=MODEL,
            response_format={"type": "json_object"},
            temperature=0.7
        )
        content = completion.choices[0].message.content
        result = json.loads(content).get("ideas", [])
        
        # Enrich results with original metadata (URL, full text)
        enhanced_ideas = []
        for idea in result:
            try:
                # Map back to original data using index
                idx = int(idea.get('source_id', -1))
                if 0 <= idx < len(collected_data):
                    original = collected_data[idx]
                    enhanced_ideas.append({
                        "name": idea['name'],
                        "pitch": idea['pitch'],
                        "source_text": original['text'],
                        "source_url": original['url'],
                        "source_origin": original['source']
                    })
            except (ValueError, IndexError):
                continue # Skip if mapping fails
                
        return enhanced_ideas
        
    except Exception as e:
        logger.error(f"AI Generation Failed: {e}")
        return []

# --- ROUTES ---
@app.errorhandler(500)
def internal_error(error):
    # Log the full error for developers, but show a generic message to the user
    logger.error(f"Server Error: {error}")
    return jsonify({"error": "Something went wrong on the server. Please try again later."}), 500

@app.errorhandler(404)
def not_found_error(error):
    return jsonify({"error": "Resource Not Found"}), 404

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/generate-ideas', methods=['POST'])
@limiter.limit("5 per minute") # Rate Limiting (Checklist #13)
async def generate():
    data = request.json
    if not data:
        return jsonify({"error": "Invalid JSON payload"}), 400

    source = data.get('source', 'all')
    collected_data = []
    
    # Gather Data
    if source in ['hn', 'all']:
        collected_data.extend(await scrape_hn_opportunities())
        
    if source in ['reddit', 'all']:
        subs = data.get('subreddits', [])
        # Validation: Reddit source requires subreddits
        if source == 'reddit' and (not subs or (isinstance(subs, list) and not any(subs))):
             return jsonify({"error": "Subreddits list cannot be empty for Reddit source"}), 400
        
        # If 'all' and no subs provided, ideally we default or skip. 
        # But if provided but empty, that's an issue? 
        # Let's be robust: If 'all' and no subs, use defaults. If 'reddit', enforce.
        if not subs: subs = ["SaaS", "startups"] 
        
        collected_data.extend(scrape_reddit_rss(subs))
        
    if source in ['reviews']:
        app_name = data.get('app_name', '')
        if not app_name or not app_name.strip():
             return jsonify({"error": "App Name cannot be empty for Reviews source"}), 400
        
        platform = data.get('platform', 'android')
        logger.info(f"Generating for App: '{app_name}' | Platform Pref: '{platform}'")
        
        reviews = scrape_reviews(app_name, platform)
        if reviews is None:
             return jsonify({"error": f"App '{app_name}' not found in {platform} store(s)."}), 404
        collected_data.extend(reviews)

    # Analyze
    ideas = generate_business_ideas(collected_data)
    
    return jsonify({
        "raw_count": len(collected_data),
        "ideas": ideas
    })

if __name__ == '__main__':
    # Prod readiness: This block is ignored by Gunicorn
    app.run(debug=True, port=5000)