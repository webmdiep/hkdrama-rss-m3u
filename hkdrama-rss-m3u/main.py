import re
import os
import time
import urllib.parse as urlparse
from urllib.parse import parse_qs
import requests
import feedparser

HEADERS = {"User-Agent": "Mozilla/5.0 (RSS-to-M3U resolver)"}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

HK_FEEDS = {
    "HK Drama": "http://allrss.se/dramas/?channel=hk-drama&nocache=1",
    "HK Variety & News": "http://allrss.se/dramas/?channel=hk-variety&nocache=1",
    "HK Drama (English Subtitles)": "http://allrss.se/dramas/?channel=hk-drama-englishsubtitles&nocache=1",
}

MEDIA_EXT = re.compile(r"\.(m3u8|mp4|mov|mpd|ts)(\?.*)?$", re.I)
EPISODE_URL = re.compile(r"[?&]episodes=\d+", re.I)
INNER_XML_HINT = re.compile(r"https?://v\.allrss\.se/v/[^\"'<>\s]+", re.I)

def parse_feed(url_or_text):
    return feedparser.parse(url_or_text)

def fetch_text(url, timeout=20):
    r = SESSION.get(url, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.text

def choose_best_media(urls):
    """Prefer HLS (.m3u8) if present; else first."""
    hls = [u for u in urls if u.lower().endswith(".m3u8") or ".m3u8" in u.lower()]
    return (hls[0] if hls else (urls[0] if urls else None))

def extract_enclosure_urls(entry):
    out = []
    if "enclosures" in entry and entry.enclosures:
        for enc in entry.enclosures:
            href = enc.get("href")
            if href:
                out.append(href)
    if not out and "links" in entry:
        for l in entry.links:
            href = l.get("href")
            if href and (l.get("rel") == "enclosure" or MEDIA_EXT.search(href)):
                out.append(href)
    # fallback to entry.link
    if not out and entry.get("link"):
        out.append(entry["link"])
    # dedupe
    dedup = []
    seen = set()
    for u in out:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup

def ensure_xml_view(url):
    # If this is an episode page, add &xml=1 to get an XML representation
    parsed = urlparse.urlparse(url)
    qs = parse_qs(parsed.query)
    if "xml" not in qs:
        sep = "&" if parsed.query else "?"
        return url + f"{sep}xml=1"
    return url

def resolve_episode_to_media(url):
    """
    Resolve an episode-level URL to one or more direct media URLs.
    Strategy:
      - If it already looks like media, return it.
      - If it's an episode page (?episodes=...), fetch XML view (&xml=1).
      - In that XML, look for <enclosure> media OR an inner v.allrss.se/v/... xml.
      - If inner v.* xml exists, fetch it and pull its enclosures.
    """
    # 1) already a media URL?
    if MEDIA_EXT.search(url):
        return [url]

    # 2) get XML view for episode (or whatever the page is)
    xml_url = ensure_xml_view(url)
    xml_text = fetch_text(xml_url)
    fp = parse_feed(xml_text)

    media = []
    # 3) direct enclosures in this XML?
    for e in fp.entries:
        encs = extract_enclosure_urls(e)
        media.extend(encs)

    # Some feeds might not populate feedparser enclosures; regex as fallback
    if not media:
        # Find inner v.* xml endpoints on the page
        inner = INNER_XML_HINT.findall(xml_text)
        # add &xml=1 if not present
        inner = [u if "xml=" in u else (u + ("&xml=1" if "?" in u else "?xml=1")) for u in inner]
        for u in inner:
            try:
                inner_text = fetch_text(u)
                inner_fp = parse_feed(inner_text)
                for e in inner_fp.entries:
                    encs = extract_enclosure_urls(e)
                    media.extend(encs)
            except Exception as ex:
                print(f"[WARN] inner xml fetch failed {u}: {ex}")

    # 4) dedupe and prefer HLS
    media = list(dict.fromkeys(media))
    # Some enclosures may still be html wrappers; keep only ones that look playable or are commonly accepted
    playable = [m for m in media if MEDIA_EXT.search(m) or ".m3u8" in m.lower()]
    return playable or media

def collect_group(group_name, feed_url, polite_sleep=0.3):
    out = []
    fp = parse_feed(feed_url)
    for e in fp.entries:
        title = e.get("title", "Untitled")
        urls = extract_enclosure_urls(e)
        if not urls:
            continue
        for u in urls:
            # If it's an episode pointer, resolve to media
            if EPISODE_URL.search(u):
                try:
                    medias = resolve_episode_to_media(u)
                    if not medias:
                        continue
                    best = choose_best_media(medias)
                    if best:
                        out.append((group_name, title, best))
                except Exception as ex:
                    print(f"[WARN] failed to resolve episode {u}: {ex}")
            else:
                # Non-episode: if already media, include; otherwise try to resolve anyway
                if MEDIA_EXT.search(u):
                    out.append((group_name, title, u))
                else:
                    try:
                        medias = resolve_episode_to_media(u)
                        best = choose_best_media(medias)
                        if best:
                            out.append((group_name, title, best))
                    except Exception:
                        pass
        time.sleep(polite_sleep)
    return out


def write_m3u(items, path="m3u-output/hk_channels.m3u"):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    seen = set()
    with open(path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for group, title, url in items:
            if url in seen:
                continue
            seen.add(url)
            f.write(f'#EXTINF:-1 group-title="{group}",{title}\n{url}\n')
    return path


def main():
    all_items = []
    for group, url in HK_FEEDS.items():
        print(f"[INFO] {group} -> {url}")
        all_items.extend(collect_group(group, url))
    out = write_m3u(all_items)
    print(f"[OK] wrote {out} with {len(all_items)} items")

if __name__ == "__main__":
    main()
