from googlesearch import search
import requests
from bs4 import BeautifulSoup
import re
import json


def fetch_website_data(url: str) -> str:
    """Fetch raw HTML/text content from a URL."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return json.dumps({'status': 'success', 'content': response.text[:5000]})  # Limit response size
    except requests.exceptions.RequestException as e:
        return json.dumps({'status': 'error', 'content': f"Error: {e}"})


def google_search(query, num_results=5):
    """Perform a Google search and return list of URLs."""
    try:
        search_results = search(query, num_results=num_results)
        results = list(search_results)
        return results
    except Exception as e:
        return []


def extracttext(html: str) -> str:
    """Extract clean text from HTML content."""
    try:
        soup = BeautifulSoup(html, 'html.parser')
        body_content = soup.body
        if body_content:
            clean_text = re.sub(r'<[^>]+>', '', body_content.get_text())
            clean_text = clean_text.replace("\n", " ")
            # Collapse multiple spaces
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()
            return clean_text
        return ""
    except Exception:
        return ""


def webscraper(links: list) -> list:
    """Scrape text content from a list of URLs."""
    final_container = []
    for url in links:
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                text = extracttext(response.text)
                if text:
                    final_container.append(text[:2000])  # Limit per-page content
        except Exception as e:
            print(f"Error scraping {url}: {e}")
            continue  # Continue to next URL instead of returning
    return final_container


def webdata(query):
    """Search the web and scrape results for a query."""
    links = google_search(query=query, num_results=5)
    if not links:
        return json.dumps({'status': 'error', 'content': 'No search results found'})
    data = webscraper(links=links)  # Fixed: pass full list, not links[0]
    if not data:
        return json.dumps({'status': 'error', 'content': 'Could not extract data from search results'})
    return json.dumps({'status': 'success', 'content': data})


# print(webdata(query="the name of india's current president")[0])