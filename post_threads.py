import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error

GRAPH_BASE = "https://graph.threads.net/v1.0"
ACCESS_TOKEN = os.environ["THREADS_ACCESS_TOKEN"]
USER_ID = os.environ["THREADS_USER_ID"]

POST_TEXT = os.environ.get(
    "THREADS_POST_TEXT",
    "Threads API 自動投稿テストです。"
)


def post_form(url, data):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {detail}", file=sys.stderr)
        raise


def main():
    # Threads API supports auto-publishing text posts at container creation time.
    # This avoids a race where an immediately published container may not yet be ready.
    published = post_form(
        f"{GRAPH_BASE}/{USER_ID}/threads",
        {
            "media_type": "TEXT",
            "text": POST_TEXT,
            "auto_publish_text": "true",
            "access_token": ACCESS_TOKEN,
        },
    )

    post_id = published.get("id")
    if not post_id:
        raise RuntimeError(f"Post ID was not returned: {published}")

    print("Published successfully:", json.dumps(published, ensure_ascii=False))


if __name__ == "__main__":
    main()
