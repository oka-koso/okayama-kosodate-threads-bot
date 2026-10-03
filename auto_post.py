# -*- coding: utf-8 -*-
import hashlib
import json
import os
import random
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from content_bank import DAILY_POSTS, SITE_PROMOS, AFFILIATE_PROMOS

GRAPH_BASE = "https://graph.threads.net/v1.0"
ACCESS_TOKEN = os.environ["THREADS_ACCESS_TOKEN"]
USER_ID = os.environ["THREADS_USER_ID"]
STATE_PATH = Path("state.json")
JST = timezone(timedelta(hours=9))

# GitHub Actions runs at these irregular minutes. Daily posting slots are selected
# deterministically from them, so reruns do not create a new schedule.
MINUTES = (7, 23, 41, 56)
START_HOUR = 7
END_HOUR = 22
MIN_GAP_MINUTES = 90
RECENT_DAILY_LIMIT = 24
STATE_KEEP_DAYS = 60


def seeded_rng(seed_text: str) -> random.Random:
    seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:16], 16)
    return random.Random(seed)


def daily_schedule(day):
    rng = seeded_rng(f"schedule-v2|{day.isoformat()}")
    count = rng.randint(3, 5)
    candidates = [
        (h, m)
        for h in range(START_HOUR, END_HOUR + 1)
        for m in MINUTES
    ]
    rng.shuffle(candidates)

    chosen = []
    for h, m in candidates:
        minute_of_day = h * 60 + m
        if all(abs(minute_of_day - (ch * 60 + cm)) >= MIN_GAP_MINUTES for ch, cm in chosen):
            chosen.append((h, m))
            if len(chosen) == count:
                break

    chosen.sort()
    return chosen


def promo_plan_for_week(day):
    iso = day.isocalendar()
    rng = seeded_rng(f"promo-v2|{iso.year}-W{iso.week:02d}")

    # Exactly two possible promo slots per week, always on separate, well-spaced days.
    day_pairs = [(0, 3), (1, 4), (2, 5), (3, 6), (0, 4), (1, 5), (2, 6)]
    d1, d2 = rng.choice(day_pairs)

    result = {}
    monday = day - timedelta(days=day.weekday())
    for i, weekday in enumerate((d1, d2)):
        target_day = monday + timedelta(days=weekday)
        slots = daily_schedule(target_day)
        slot = rng.choice(slots)
        # Mix site and affiliate, while keeping the combined cap at two/week.
        kind = "affiliate" if i == rng.randint(0, 1) and AFFILIATE_PROMOS else "site"
        result[(target_day.isoformat(), slot[0], slot[1])] = kind

    # Make sure at least one normal site introduction is present if both became affiliate.
    kinds = list(result.values())
    if kinds.count("affiliate") == 2:
        first = next(iter(result))
        result[first] = "site"
    return result


def load_state():
    if not STATE_PATH.exists():
        return {"posted_slots": [], "recent_daily_hashes": [], "history": []}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        data.setdefault("posted_slots", [])
        data.setdefault("recent_daily_hashes", [])
        data.setdefault("history", [])
        return data
    except Exception:
        return {"posted_slots": [], "recent_daily_hashes": [], "history": []}


def prune_state(state, today):
    cutoff = today - timedelta(days=STATE_KEEP_DAYS)
    keep_prefixes = set()
    d = cutoff
    while d <= today:
        keep_prefixes.add(d.isoformat())
        d += timedelta(days=1)

    state["posted_slots"] = [
        s for s in state["posted_slots"]
        if s.split("T", 1)[0] in keep_prefixes
    ]
    state["history"] = [
        h for h in state["history"]
        if h.get("date") in keep_prefixes
    ][-250:]
    state["recent_daily_hashes"] = state["recent_daily_hashes"][-RECENT_DAILY_LIMIT:]


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


def publish(text):
    result = post_form(
        f"{GRAPH_BASE}/{USER_ID}/threads",
        {
            "media_type": "TEXT",
            "text": text,
            "auto_publish_text": "true",
            "access_token": ACCESS_TOKEN,
        },
    )
    if not result.get("id"):
        raise RuntimeError(f"Post ID was not returned: {result}")
    return result["id"]


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def pick_daily_post(state, day, hour, minute):
    recent = set(state["recent_daily_hashes"])
    candidates = [p for p in DAILY_POSTS if text_hash(p) not in recent]
    if not candidates:
        candidates = DAILY_POSTS[:]
        state["recent_daily_hashes"] = []

    rng = seeded_rng(f"daily-text-v2|{day.isoformat()}|{hour:02d}:{minute:02d}")
    return rng.choice(candidates)


def pick_promo(kind, day, hour, minute):
    bank = AFFILIATE_PROMOS if kind == "affiliate" else SITE_PROMOS
    rng = seeded_rng(f"promo-text-v2|{kind}|{day.isoformat()}|{hour:02d}:{minute:02d}")
    return rng.choice(bank)


def main():
    now = datetime.now(JST)
    day = now.date()
    slot = (now.hour, now.minute)
    schedule = daily_schedule(day)

    print("JST now:", now.isoformat(timespec="minutes"))
    print("Today's schedule:", ", ".join(f"{h:02d}:{m:02d}" for h, m in schedule))

    if slot not in schedule:
        print("Not a posting slot. Nothing to do.")
        return

    state = load_state()
    prune_state(state, day)

    slot_key = f"{day.isoformat()}T{now.hour:02d}:{now.minute:02d}"
    if slot_key in state["posted_slots"]:
        print("This slot was already posted. Skipping duplicate.")
        return

    promo_plan = promo_plan_for_week(day)
    kind = promo_plan.get((day.isoformat(), now.hour, now.minute), "daily")

    if kind == "daily":
        text = pick_daily_post(state, day, now.hour, now.minute)
    else:
        text = pick_promo(kind, day, now.hour, now.minute)

    post_id = publish(text)
    print(f"Published {kind} post: {post_id}")

    state["posted_slots"].append(slot_key)
    if kind == "daily":
        h = text_hash(text)
        state["recent_daily_hashes"].append(h)
        state["recent_daily_hashes"] = state["recent_daily_hashes"][-RECENT_DAILY_LIMIT:]

    state["history"].append({
        "date": day.isoformat(),
        "time": f"{now.hour:02d}:{now.minute:02d}",
        "kind": kind,
        "post_id": post_id,
        "text_hash": text_hash(text),
    })
    state["history"] = state["history"][-250:]
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
