#!/usr/bin/env python3
# snowy: Generates an HTML encyclopedia from a wordlist using the Wikipedia/MediaWiki APIs.
# Copyright (C) 2026 saifnama
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
snowy: Generates an HTML encyclopedia from a wordlist using the Wikipedia/MediaWiki APIs.

Features:
- Fetches Wikipedia summaries (first paragraph only) and first images
- Includes Wikidata IDs
- Handles disambiguation pages
- Handles missing Wikipedia pages
- Case-insensitive word merging (e.g., "India" and "india" become one entry)
- High-quality images with proper captions from Wikipedia
"""

import requests
import asyncio
import argparse
import html
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, unquote

# SAFETY SETTINGS: Adjust these to be more or less gentle on Wikipedia's API
API_DELAY = 0.5          # Delay (seconds) between requests in each worker
CONCURRENCY_LIMIT = 3    # Max number of words to fetch at the exact same time
MAX_RETRIES = 5          # Number of times to retry if rate-limited (429)
BACKOFF_FACTOR = 2       # Multiplier for wait time between retries


# Wikipedia API endpoints
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Proper User-Agent (required by Wikipedia API)
USER_AGENT = 'Snowy/1.0 (Educational tool; Python script)'

# Create a session with proper User-Agent
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': USER_AGENT})

# File extensions to exclude (audio/video files)
EXCLUDED_EXTENSIONS = ['.ogg', '.ogv', '.webm', '.mp3', '.mp4', '.wav', '.flac', '.oga']


def fetch_with_retry(url, params=None):
    """
    Fetch URL with exponential backoff for 429 (Too Many Requests) errors.
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = SESSION.get(url, params=params, timeout=30)
            
            # If we get a 429, wait and retry
            if response.status_code == 429:
                if attempt < MAX_RETRIES:
                    wait_time = BACKOFF_FACTOR ** (attempt + 1)
                    if 'Retry-After' in response.headers:
                        try:
                            wait_time = float(response.headers['Retry-After'])
                        except ValueError:
                            pass
                    # Add a bit of jitter or strict wait
                    time.sleep(wait_time)
                    continue
                else:
                    response.raise_for_status()
            
            response.raise_for_status()
            return response
            
        except requests.exceptions.RequestException as e:
            # If it's the last attempt, raise the error
            if attempt == MAX_RETRIES:
                raise e
            # If it's a 429 inside an exception (rare but possible), wait
            # Otherwise, for other network errors, maybe we shouldn't retry individually 
            # unless we want to be very robust. For now, focus on 429 loop above.
            raise e


def normalize_word(word):
    """Normalize word for case-insensitive comparison."""
    return word.strip().lower()


def merge_words(words):
    """
    Merge words that are the same when case is ignored.
    Returns dict: {normalized_word: preferred_display_form}
    The preferred form is the one with a capital letter if it exists, otherwise the first occurrence.
    """
    word_groups = defaultdict(list)
    
    for word in words:
        word = word.strip()
        if word:
            normalized = normalize_word(word)
            word_groups[normalized].append(word)
    
    merged = {}
    for normalized, variants in word_groups.items():
        # Prefer capitalized version, otherwise first occurrence
        capitalized = [w for w in variants if w[0].isupper()]
        if capitalized:
            merged[normalized] = capitalized[0]
        else:
            merged[normalized] = variants[0]
    
    return merged


def is_valid_image_url(url):
    """Check if the URL is a valid image (not audio/video)."""
    if not url:
        return False
    url_lower = url.lower()
    for ext in EXCLUDED_EXTENSIONS:
        if url_lower.endswith(ext):
            return False
    return True


def get_first_paragraph(text):
    """Extract only the first paragraph from the text."""
    if not text:
        return text
    
    # Split by double newlines (paragraph separator)
    paragraphs = text.split('\n\n')
    if paragraphs:
        first_para = paragraphs[0].strip()
        # Also handle single newline paragraphs
        if '\n' in first_para:
            # Take content up to the first newline if it's a short intro
            parts = first_para.split('\n')
            # If the first part is reasonably long, use just that
            if len(parts[0]) > 100:
                return parts[0].strip()
        return first_para
    return text


async def get_inline_image_caption(page_title, image_filename):
    """
    Try to find the caption for a specific image filename directly in the article HTML.
    Attempts to match the filename within the lead section's HTML.
    """
    def fetch():
        # Use the parse API to get only section 0 (lead section with infobox)
        params = {
            'action': 'parse',
            'page': page_title,
            'prop': 'text',
            'format': 'json',
            'section': 0,  # Only the lead section with infobox
            'redirects': 1
        }
        
        response = fetch_with_retry(WIKIPEDIA_API, params=params)
        return response.json()

    try:
        data = await asyncio.to_thread(fetch)
        
        html_content = data.get('parse', {}).get('text', {}).get('*', '')
        
        if not html_content:
            return None
        
        # Priority 1: Extract the infobox and find colspan td elements (these contain image captions)
        infobox_pattern = r'<table[^>]*class="[^"]*infobox[^"]*"[^>]*>(.*?)</table>'
        infobox_match = re.search(infobox_pattern, html_content, re.DOTALL | re.IGNORECASE)
        
        if infobox_match:
            infobox = infobox_match.group(1)
            
            # Find colspan td elements - these typically contain image captions
            colspan_pattern = r'<td[^>]*colspan[^>]*>(.*?)</td>'
            colspans = re.findall(colspan_pattern, infobox, re.DOTALL)
            
            # The first short colspan td after the title is usually the image caption
            for td_content in colspans:
                clean_cap = re.sub(r'<[^>]+>', '', td_content).strip()
                clean_cap = html.unescape(clean_cap)
                
                # Skip captions with newlines (these are usually multiple labels merged)
                if '\n' in clean_cap:
                    continue
                
                # Skip very short, very long, or title-like captions
                # Image captions are typically 10-200 chars (increased minimum to filter labels)
                if clean_cap and 10 <= len(clean_cap) <= 200:
                    # Skip if it looks like a taxonomy/classification/conservation entry
                    skip_words = ['kingdom:', 'phylum:', 'class:', 'order:', 'family:', 
                                  'genus:', 'species:', 'binomial', 'clade:',
                                  'domesticated', 'endangered', 'extinct', 'vulnerable',
                                  'least concern', 'conservation status', 'scientific classification',
                                  'domain:', 'division:', 'subfamily:', 'tribe:']
                    if any(skip in clean_cap.lower() for skip in skip_words):
                        continue
                    
                    # Skip single-word infobox labels (common in country/org infoboxes)
                    skip_labels = ['flag', 'emblem', 'anthem', 'coat of arms', 'seal', 
                                   'logo', 'motto', 'capital', 'official', 'government',
                                   'currency', 'language', 'religion', 'national anthem',
                                   'area', 'population', 'gdp', 'hdi', 'drives on']
                    if clean_cap.lower() in skip_labels:
                        continue
                    
                    # Skip if caption starts with a label pattern (e.g., "Anthem:", "Capital:", etc.)
                    label_patterns = ['anthem:', 'capital:', 'currency:', 'official', 'language:',
                                      'government:', 'president:', 'prime minister:', 'area:',
                                      'population:', 'gdp:', 'hdi:', 'calling code:']
                    if any(clean_cap.lower().startswith(lp) for lp in label_patterns):
                        continue
                    
                    # Skip scientific/binomial names (italicized Latin names like "Felis catus")
                    # Pattern: Contains "Linnaeus" or year like ", 1758" or author citation
                    if re.search(r'(Linnaeus|, \d{4}|\[\d+\])', clean_cap):
                        continue
                    
                    # Skip if it looks like a scientific name (two capitalized words)
                    if re.match(r'^[A-Z][a-z]+ [a-z]+$', clean_cap.split('[')[0].strip()):
                        continue
                    
                    return clean_cap
            
            # Also try to find figcaption within the infobox
            figcaption_pattern = r'<figcaption[^>]*>(.*?)</figcaption>'
            figcaps = re.findall(figcaption_pattern, infobox, re.DOTALL)
            for cap in figcaps:
                clean_cap = re.sub(r'<[^>]+>', '', cap).strip()
                clean_cap = html.unescape(clean_cap)
                if clean_cap and len(clean_cap) > 3:
                    return clean_cap[:300] if len(clean_cap) > 300 else clean_cap
        
        # Priority 2: Look for figcaption on the page (outside infobox)
        figcaption_pattern = r'<figcaption[^>]*>(.*?)</figcaption>'
        figcaptions = re.findall(figcaption_pattern, html_content, re.DOTALL | re.IGNORECASE)
        
        if figcaptions:
            # Get the first non-empty figcaption
            for cap in figcaptions:
                # Clean HTML tags
                clean_cap = re.sub(r'<[^>]+>', '', cap)
                clean_cap = html.unescape(clean_cap).strip()
                if clean_cap and len(clean_cap) > 3:
                    return clean_cap[:300] if len(clean_cap) > 300 else clean_cap
        
        # Priority 4: Look for thumbcaption class (older Wikipedia style)
        thumbcaption_pattern = r'<div[^>]*class="[^"]*thumbcaption[^"]*"[^>]*>(.*?)</div>'
        thumbcaptions = re.findall(thumbcaption_pattern, html_content, re.DOTALL | re.IGNORECASE)
        
        if thumbcaptions:
            for cap in thumbcaptions:
                # Remove magnify link and other nested elements
                cap = re.sub(r'<div[^>]*class="[^"]*magnify[^"]*"[^>]*>.*?</div>', '', cap, flags=re.DOTALL)
                clean_cap = re.sub(r'<[^>]+>', '', cap)
                clean_cap = html.unescape(clean_cap).strip()
                if clean_cap and len(clean_cap) > 3:
                    return clean_cap[:300] if len(clean_cap) > 300 else clean_cap
        
        # Priority 5: Look for mw-mmv-title span (MediaViewer title)
        mmv_pattern = r'<span[^>]*class="[^"]*mw-mmv-title[^"]*"[^>]*>(.*?)</span>'
        mmv_titles = re.findall(mmv_pattern, html_content, re.DOTALL | re.IGNORECASE)
        
        if mmv_titles:
            for cap in mmv_titles:
                clean_cap = re.sub(r'<[^>]+>', '', cap)
                clean_cap = html.unescape(clean_cap).strip()
                if clean_cap and len(clean_cap) > 3:
                    return clean_cap[:300] if len(clean_cap) > 300 else clean_cap
        
        return None
        
    except Exception:
        return None


async def get_image_metadata_caption(image_url):
    """
    Get the image description from Commons/Wikipedia file metadata.
    This is the caption shown on the image detail page (fallback).
    """
    def fetch():
        # Extract filename from URL
        filename = unquote(image_url.split('/')[-1])
        file_title = f"File:{filename}"
        
        # Get image info with extended metadata
        params = {
            'action': 'query',
            'titles': file_title,
            'prop': 'imageinfo',
            'iiprop': 'extmetadata',
            'format': 'json'
        }
        
        response = fetch_with_retry(WIKIPEDIA_API, params=params)
        return response.json()

    try:
        data = await asyncio.to_thread(fetch)
        
        pages = data.get('query', {}).get('pages', {})
        if not pages:
            return None
        
        page = list(pages.values())[0]
        imageinfo = page.get('imageinfo', [{}])[0]
        metadata = imageinfo.get('extmetadata', {})
        
        # Try to get caption from various metadata fields
        # Priority: ImageDescription > ObjectName > Caption
        for field in ['ImageDescription', 'ObjectName', 'Caption']:
            if field in metadata:
                raw_caption = metadata[field].get('value', '')
                if raw_caption:
                    # Clean HTML from caption
                    caption = re.sub(r'<[^>]+>', '', raw_caption)
                    caption = html.unescape(caption).strip()
                    
                    # Skip if it's just the filename or too short
                    if len(caption) > 10:
                        # Truncate if too long
                        if len(caption) > 300:
                            caption = caption[:297] + '...'
                        return caption
        
        return None
        
    except Exception:
        return None


async def get_wikipedia_page_info(title):
    """
    Fetch Wikipedia page info including extract, image, and Wikidata ID.
    Returns a dict with page information.
    """
    result = {
        'title': title,
        'exists': False,
        'is_disambiguation': False,
        'extract': None,
        'image_url': None,
        'image_caption': None,
        'wikipedia_url': None,
        'wikidata_id': None,
        'error': None
    }
    
    def fetch():
        # First, get page info with extract and pageprops
        params = {
            'action': 'query',
            'titles': title,
            'prop': 'extracts|pageprops|pageimages|info',
            'exintro': True,
            'explaintext': True,
            'ppprop': 'disambiguation|wikibase_item',
            'piprop': 'original',
            'inprop': 'url',
            'format': 'json',
            'redirects': 1
        }
        
        response = fetch_with_retry(WIKIPEDIA_API, params=params)
        return response.json()

    try:
        data = await asyncio.to_thread(fetch)
        
        pages = data.get('query', {}).get('pages', {})
        
        if not pages:
            result['error'] = "No response from Wikipedia API"
            return result
        
        page_id = list(pages.keys())[0]
        
        if page_id == '-1':
            result['exists'] = False
            result['error'] = "Wikipedia page isn't available for this term."
            return result
        
        page = pages[page_id]
        result['exists'] = True
        actual_title = page.get('title', title)
        result['title'] = actual_title
        result['wikipedia_url'] = page.get('fullurl', f"https://en.wikipedia.org/wiki/{quote(title)}")
        
        # Check for disambiguation
        pageprops = page.get('pageprops', {})
        if 'disambiguation' in pageprops:
            result['is_disambiguation'] = True
            result['extract'] = f"This term has multiple meanings. Please visit the Wikipedia page for disambiguation."
        else:
            # Get only the first paragraph
            full_extract = page.get('extract', 'No description available.')
            result['extract'] = get_first_paragraph(full_extract)
        
        # Get Wikidata ID
        result['wikidata_id'] = pageprops.get('wikibase_item', None)
        
        # Get the main page image (from pageimages API - this is the infobox/lead image)
        original_img = page.get('original', {})
        if original_img and is_valid_image_url(original_img.get('source')):
            result['image_url'] = original_img.get('source')
        
        # If no valid image from pageimages, try to get the first valid image from page content
        if not result['image_url']:
            result['image_url'] = await get_first_valid_image(actual_title)
        
        # Get proper image caption - use the metadata from the actual image file
        # Priority: Image metadata from Commons (this is the actual caption of the displayed image)
        if result['image_url']:
            # Extract filename for matching
            image_filename = unquote(result['image_url'].split('/')[-1])
            
            # Priority 1: Image metadata from Commons/Wikipedia file page
            # This is the caption of the ACTUAL image being displayed
            metadata_caption = await get_image_metadata_caption(result['image_url'])
            if metadata_caption:
                result['image_caption'] = metadata_caption
            else:
                # Priority 2: Fallback to inline caption from the Wikipedia article page
                inline_caption = await get_inline_image_caption(actual_title, image_filename)
                if inline_caption:
                    result['image_caption'] = inline_caption
                else:
                    # Final fallback: use the page title
                    result['image_caption'] = actual_title
        
    except requests.exceptions.RequestException as e:
        result['error'] = f"Network error: {str(e)}"
    except Exception as e:
        result['error'] = f"Error fetching data: {str(e)}"
    
    return result


async def get_first_valid_image(title):
    """
    Get the first valid image from a Wikipedia page.
    Uses the images API and filters for actual photos.
    """
    def fetch():
        params = {
            'action': 'query',
            'titles': title,
            'prop': 'images',
            'imlimit': 50,
            'format': 'json',
            'redirects': 1
        }
        
        response = fetch_with_retry(WIKIPEDIA_API, params=params)
        return response.json()

    try:
        data = await asyncio.to_thread(fetch)
        
        pages = data.get('query', {}).get('pages', {})
        if not pages:
            return None
        
        page = list(pages.values())[0]
        images = page.get('images', [])
        
        # Patterns to exclude (non-content images)
        excluded_patterns = [
            'commons-logo', 'wiki', 'icon', 'symbol', 'logo', 'ambox', 
            'edit-', 'lock-', 'padlock', 'question_mark', 'red_pencil', 
            'disambig', 'stub', 'portal', 'folder', 'crystal', 'gnome',
            'nuvola', 'fairuse', 'pd-icon', 'merge', 'split', 'move',
            'speedy', 'afd', 'rfd', 'cfd', 'tfd', 'mfd', 'ifd',
            'featured', 'good_article', 'bclass', 'cclass', 'audio',
            'speaker', 'sound', 'headphones', 'loudspeaker', 'info',
            'increase', 'decrease', 'steady', 'green_arrow', 'red_arrow'
        ]
        
        # Extensions to exclude
        excluded_extensions = ['.svg', '.ogg', '.ogv', '.webm', '.mp3', '.mp4', '.wav', '.oga', '.flac']
        
        for img in images:
            img_title = img.get('title', '')
            img_lower = img_title.lower()
            
            # Skip excluded patterns
            if any(pattern in img_lower for pattern in excluded_patterns):
                continue
            
            # Skip non-image files
            if any(img_lower.endswith(ext) for ext in excluded_extensions):
                continue
            
            # Get full image URL
            image_url = await get_image_url(img_title)
            
            if image_url and is_valid_image_url(image_url):
                return image_url
        
        return None
        
    except Exception:
        return None


async def get_image_url(file_title):
    """Get full URL for an image file."""
    def fetch():
        params = {
            'action': 'query',
            'titles': file_title,
            'prop': 'imageinfo',
            'iiprop': 'url',
            'format': 'json'
        }
        
        response = fetch_with_retry(WIKIPEDIA_API, params=params)
        return response.json()

    try:
        data = await asyncio.to_thread(fetch)
        
        pages = data.get('query', {}).get('pages', {})
        if not pages:
            return None
        
        page = list(pages.values())[0]
        imageinfo = page.get('imageinfo', [{}])[0]
        
        return imageinfo.get('url')
        
    except Exception:
        return None


def generate_html(entries, output_file):
    """Generate the HTML encyclopedia file."""
    
    html_content = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Encyclopedia</title>
    <style>
        :root {
            --bg-color: #f8f9fa;
            --text-color: #202122;
            --link-color: #0645ad;
            --link-visited: #0b0080;
            --border-color: #a2a9b1;
            --entry-bg: #ffffff;
            --header-bg: #f0f0f0;
            --caption-bg: #f8f9fa;
            --error-color: #d33;
            --disambig-color: #f8d7da;
            --wikidata-color: #006699;
        }
        
        * {
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Liberation Sans', sans-serif;
            line-height: 1.6;
            color: var(--text-color);
            background-color: var(--bg-color);
            margin: 0;
            padding: 20px;
        }
        
        .container {
            max-width: 90%;
            margin: 0 auto;
        }
        
        .entry {
            background: var(--entry-bg);
            border: 1px solid var(--border-color);
            margin-bottom: 25px;
            border-radius: 4px;
            overflow: hidden;
        }
        
        .entry-header {
            background: var(--header-bg);
            padding: 15px 20px;
            border-bottom: 1px solid var(--border-color);
        }
        
        .entry-header h2 {
            margin: 0;
            font-size: 1.5em;
            font-weight: 500;
        }
        
        .entry-header h2 a {
            color: var(--link-color);
            text-decoration: none;
        }
        
        .entry-header h2 a:hover {
            text-decoration: underline;
        }
        
        .wikidata-id {
            font-size: 0.85em;
            color: var(--wikidata-color);
            margin-left: 10px;
            font-weight: normal;
        }
        
        .wikidata-id a {
            color: var(--wikidata-color);
            text-decoration: none;
        }
        
        .wikidata-id a:hover {
            text-decoration: underline;
        }
        
        .entry-content {
            padding: 20px;
            display: flex;
            gap: 20px;
            flex-wrap: wrap;
        }
        
        .entry-text {
            flex: 1;
            min-width: 300px;
        }
        
        .entry-text p {
            margin: 0 0 15px 0;
        }
        
        .entry-text p:last-child {
            margin-bottom: 0;
        }
        
        .entry-image {
            flex: 0 0 300px;
            max-width: 100%;
        }
        
        @media (max-width: 768px) {
            .entry-content {
                flex-direction: column;
            }
            
            .entry-image {
                flex: none;
                width: 100%;
            }
        }
        
        .image-container {
            border: 1px solid var(--border-color);
            background: var(--caption-bg);
            padding: 5px;
        }
        
        .image-container img {
            width: 100%;
            height: auto;
            display: block;
        }
        
        .image-caption {
            font-size: 0.85em;
            color: #54595d;
            padding: 8px;
            text-align: center;
            line-height: 1.4;
        }
        
        .disambiguation-notice {
            background: var(--disambig-color);
            border: 1px solid #f5c6cb;
            color: #721c24;
            padding: 10px 15px;
            border-radius: 4px;
            margin-bottom: 15px;
            font-size: 0.9em;
        }
        
        .error-notice {
            color: var(--error-color);
            font-style: italic;
        }
        
        .no-wikidata {
            color: #666;
            font-style: italic;
        }
        
        .wikipedia-link {
            display: inline-block;
            margin-top: 10px;
            font-size: 0.9em;
        }
        
        footer {
            text-align: center;
            padding: 30px 0;
            border-top: 1px solid var(--border-color);
            margin-top: 30px;
            color: #54595d;
            font-size: 0.9em;
        }
    </style>
</head>
<body>
    <div class="container">
'''
    
    # Add entries directly (no header, stats, or TOC)
    for entry in sorted(entries, key=lambda x: x['title'].lower()):
        anchor = html.escape(entry['title'].replace(' ', '_'))
        title_display = html.escape(entry['title'])
        
        html_content += f'''
        <article class="entry" id="{anchor}">
            <div class="entry-header">
                <h2>'''
        
        # Title with Wikipedia link if available
        if entry['wikipedia_url']:
            html_content += f'<a href="{html.escape(entry["wikipedia_url"])}" target="_blank">{title_display}</a>'
        else:
            html_content += title_display
        
        # Wikidata ID
        if entry['wikidata_id']:
            wikidata_url = f"https://www.wikidata.org/wiki/{entry['wikidata_id']}"
            html_content += f' <span class="wikidata-id">[<a href="{wikidata_url}" target="_blank">{entry["wikidata_id"]}</a>]</span>'
        else:
            html_content += ' <span class="wikidata-id no-wikidata">[Wikidata ID not available]</span>'
        
        html_content += '''</h2>
            </div>
            <div class="entry-content">
                <div class="entry-text">
'''
        
        # Disambiguation notice
        if entry['is_disambiguation']:
            html_content += '''                    <div class="disambiguation-notice">
                        ⚠️ <strong>Disambiguation:</strong> This term has multiple meanings on Wikipedia.
                    </div>
'''
        
        # Content or error
        if entry['exists']:
            extract = html.escape(entry['extract'] or 'No description available.')
            html_content += f'                    <p>{extract}</p>\n'
            
            if entry['wikipedia_url']:
                html_content += f'                    <p class="wikipedia-link">🔗 <a href="{html.escape(entry["wikipedia_url"])}" target="_blank">Read more on Wikipedia</a></p>\n'
        else:
            error_msg = html.escape(entry['error'] or "Wikipedia page isn't available for this term.")
            html_content += f'                    <p class="error-notice">❌ {error_msg}</p>\n'
        
        html_content += '                </div>\n'
        
        # Image section
        if entry['image_url']:
            image_url = html.escape(entry['image_url'])
            caption = html.escape(entry['image_caption'] or entry['title'])
            html_content += f'''                <div class="entry-image">
                    <div class="image-container">
                        <img src="{image_url}" alt="{html.escape(entry['title'])}" loading="lazy">
                        <div class="image-caption">{caption}</div>
                    </div>
                </div>
'''
        
        html_content += '''            </div>
        </article>
'''
    
    html_content += '''
    </div>
</body>
</html>
'''
    
    # Write to file
    output_path = Path(output_file)
    output_path.write_text(html_content, encoding='utf-8')
    # Use pastel green specifically for the final save and done messages
    PASTEL_GREEN = '\033[38;2;166;209;137m'
    RESET = '\033[0m'
    print(f"{PASTEL_GREEN}File saved to: {output_path.absolute()}{RESET}")


# Terminal styling constants (Catppuccin-inspired TrueColor)
RESET = '\033[0m'
BOLD = '\033[1m'
GRAY = '\033[90m'

# User-requested hex codes
HOT_PINK = '\033[38;2;255;51;153m'    # Exact #FF3399 Pink
SKY_BLUE = '\033[38;2;0;191;255m'    # Vibrant Sky Blue
LIGHT_SKY_BLUE = '\033[38;2;135;206;250m' # Light Sky Blue (#87CEFA)
DARK_TEAL = '\033[38;2;0;102;102m'   # Darker Teal for entries
WHITE = '\033[97m'                   # Pure white
PASTEL_GREEN = '\033[38;2;166;227;161m' # #a6e3a1 (Brighter Green)
PASTEL_GOLD = '\033[38;2;249;226;175m'  # #f9e2af (Brighter Gold)
PASTEL_RED = '\033[38;2;231;130;132m'   # #e78284 (Error)
PASTEL_BLUE = '\033[38;2;145;215;227m'  # #91d7e3 (Minimal Blue)

# Map standard names to pastel versions for consistency
GREEN = PASTEL_GREEN
YELLOW = PASTEL_GOLD
RED = PASTEL_RED
CYAN = PASTEL_BLUE
SUCCESS_GREEN = PASTEL_GREEN
DISAMBIG_GOLD = PASTEL_GOLD
ERROR_RED = PASTEL_RED

def print_progress_bar(iteration, total, start_time, length=50):
    """Print a sleek pink progress bar similar to the user-provided image."""
    if iteration == 0:
        elapsed_time = 0
        rate = 0
        eta_str = "-:--:--"
    else:
        elapsed_time = time.time() - start_time
        rate = iteration / elapsed_time
        remaining = total - iteration
        eta_seconds = remaining / rate
        
        m, s = divmod(int(eta_seconds), 60)
        h, m = divmod(m, 60)
        if h > 0:
            eta_time = f"{h}:{m:02d}:{s:02d}"
        else:
            eta_time = f"{m}:{s:02d}"
        eta_str = f"{WHITE}eta{RESET} {SKY_BLUE}{eta_time}{RESET}"
    
    filled_length = int(length * iteration // total)
    # Use horizontal bar character ━ (U+2501)
    bar = HOT_PINK + '━' * filled_length + RESET + GRAY + '━' * (length - filled_length) + RESET
    
    # Info string: items/total and ETA
    info = f" {DARK_TEAL}{iteration}/{total} entries {RESET}{GRAY}|{RESET} {eta_str}"
    
    # Pad with spaces to clear any previous trailing characters
    sys.stdout.write(f'\r{bar} {info}{RESET}    ')
    sys.stdout.flush()


async def process_word(semaphore, display_word, i, total, start_time, verbose, entries_list):
    """Worker function to process a single word with concurrency control."""
    async with semaphore:
        entry = await get_wikipedia_page_info(display_word)
        entries_list.append(entry)
        
        # In async mode, we need a way to track which index this was
        # But for the progress bar, it's just about count
        
        if verbose:
            # We don't print the [i/total] prefix here to avoid interleaved output issues,
            # or we accept that order might be slightly shuffled.
            # Actually, with a semaphore of 5, it's manageable.
            status = ""
            if entry['exists']:
                if entry['is_disambiguation']:
                    status = f"{DISAMBIG_GOLD}Disambiguation{RESET}"
                else:
                    img_status = "Success (with image)" if entry['image_url'] else "Success (no image)"
                    status = f"{SUCCESS_GREEN}{img_status}{RESET}"
            else:
                status = f"{ERROR_RED}Not found{RESET}"
            
            print(f"  Fetching: {display_word.ljust(20)} - {status}")
        else:
            # Update progress bar based on how many have finished so far
            current_done = len(entries_list)
            print_progress_bar(current_done, total, start_time, length=50)
        
        # Minor delay to be extra safe with the API
        await asyncio.sleep(API_DELAY)

async def async_main():
    if '-h' in sys.argv or '--help' in sys.argv:
        print()
    else:
        print("snowy is running...")
    parser = argparse.ArgumentParser(
        description=f'{LIGHT_SKY_BLUE}snowy{RESET}: {WHITE}Generates an HTML encyclopedia from a wordlist using the Wikipedia/MediaWiki APIs.{RESET}',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage=f'py snowy.py {SUCCESS_GREEN}-i{RESET} FILE {PASTEL_GOLD}-o{RESET} OUTPUT [options]',
        add_help=False
    )
    
    group = parser.add_argument_group(f'{BOLD}Options{RESET}')
    
    group.add_argument(
        '-i', '--input',
        required=True,
        metavar='',
        help='Path to the input text file (e.g., words.txt)'
    )
    
    group.add_argument(
        '-o', '--output',
        default='encyclopedia.html',
        metavar='',
        help='Path for the output HTML file (default: encyclopedia.html)'
    )
    
    group.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable detailed status for each word'
    )
    
    group.add_argument(
        '-h', '--help',
        action='help',
        help='Show this help message and exit'
    )
    
    args = parser.parse_args()
    
    # Read input file
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"{RED}Error: Input file '{args.input}' not found.{RESET}")
        sys.exit(1)
    
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            words = [line.strip() for line in f if line.strip()]
    except Exception as e:
        print(f"{RED}Error reading input file: {e}{RESET}")
        sys.exit(1)
    
    if not words:
        print(f"{RED}Error: Input file is empty or contains no valid words.{RESET}")
        sys.exit(1)
    
    print(f"Read {len(words)} words from {input_path}")
    
    # Merge case-insensitive duplicates
    merged_words = merge_words(words)
    print(f"After merging case variants: {len(merged_words)} unique entries\n")
    
    # Fetch Wikipedia data for each word
    entries = []
    total = len(merged_words)
    start_time = time.time()
    
    if not args.verbose:
        print("Processing entries")
        print_progress_bar(0, total, start_time, length=50)
    else:
        print("Starting data fetch (verbose mode)...")
    
    # Async orchestration
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT) 
    tasks = []
    for i, (normalized, display_word) in enumerate(merged_words.items(), 1):
        tasks.append(process_word(semaphore, display_word, i, total, start_time, args.verbose, entries))
    
    await asyncio.gather(*tasks)
    
    # Generate HTML
    print(f"\n{BOLD}Generating HTML encyclopedia...{RESET}")
    generate_html(entries, args.output)
    
    # Summary
    print(f"\n{BOLD}{WHITE}Summary:{RESET}")
    print(f"   Total entries:  {len(entries)}")
    print(f"   Successful:     {sum(1 for e in entries if e['exists'] and not e['is_disambiguation'])}")
    print(f"   Disambiguation: {sum(1 for e in entries if e['is_disambiguation'])}")
    print(f"   Not found:      {sum(1 for e in entries if not e['exists'])}")
    print(f"   With images:    {sum(1 for e in entries if e['image_url'])}")
    print(f"{PASTEL_GREEN}Done!{RESET}")


def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print(f"\n{RED}Process interrupted by user.{RESET}")
        sys.exit(1)


if __name__ == '__main__':
    main()
