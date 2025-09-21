import feedparser

feed_url = "http://allrss.se/dramas"
feed = feedparser.parse(feed_url)

with open("allrss.m3u", "w", encoding="utf-8") as f:
    f.write("#EXTM3U\n")
    for entry in feed.entries:
        if "enclosures" in entry and entry.enclosures:
            url = entry.enclosures[0].href
            title = entry.title
            f.write(f'#EXTINF:-1,{title}\n{url}\n')
