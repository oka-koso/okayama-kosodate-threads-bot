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

START_HOUR = 7
END_HOUR = 22
SLOT_MINUTES = (0, 15, 30, 45)
PLANNED_MIN_GAP_MINUTES = 105
ACTUAL_MIN_GAP_MINUTES = 75
RECENT_DAILY_LIMIT = 200
STATE_KEEP_DAYS = 60


def seeded_rng(seed_text: str) -> random.Random:
    seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:16], 16)
    return random.Random(seed)


def daily_schedule(day):
    if day.isoformat() <= "2026-10-05":
        rng = seeded_rng(f"schedule-v3|{day.isoformat()}")
        count = rng.randint(3, 5)
    else:
        rng = seeded_rng(f"schedule-v4|{day.isoformat()}")
        count = rng.randint(4, 7)
    candidates = [
        (h, m)
        for h in range(START_HOUR, END_HOUR + 1)
        for m in SLOT_MINUTES
    ]
    rng.shuffle(candidates)

    chosen = []
    for h, m in candidates:
        minute_of_day = h * 60 + m
        if all(abs(minute_of_day - (ch * 60 + cm)) >= PLANNED_MIN_GAP_MINUTES for ch, cm in chosen):
            chosen.append((h, m))
            if len(chosen) == count:
                break

    chosen.sort()
    return chosen


def promo_plan_for_week(day):
    iso = day.isocalendar()
    rng = seeded_rng(f"promo-v3|{iso.year}-W{iso.week:02d}")

    day_pairs = [(0, 3), (1, 4), (2, 5), (3, 6), (0, 4), (1, 5), (2, 6)]
    d1, d2 = rng.choice(day_pairs)

    result = {}
    monday = day - timedelta(days=day.weekday())
    affiliate_index = rng.randint(0, 1) if AFFILIATE_PROMOS else -1

    for i, weekday in enumerate((d1, d2)):
        target_day = monday + timedelta(days=weekday)
        slots = daily_schedule(target_day)
        slot = rng.choice(slots)
        kind = "affiliate" if i == affiliate_index else "site"
        result[(target_day.isoformat(), slot[0], slot[1])] = kind

    return result


def load_state():
    if not STATE_PATH.exists():
        return {"posted_slots": [], "recent_daily_hashes": [], "used_text_hashes": [], "history": []}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        data.setdefault("posted_slots", [])
        data.setdefault("recent_daily_hashes", [])
        data.setdefault("history", [])
        data.setdefault("used_text_hashes", [])
        # One-time migration: permanently remember every text hash already present in history.
        migrated = {h.get("text_hash") for h in data["history"] if h.get("text_hash")}
        migrated.update(data.get("recent_daily_hashes", []))
        migrated.update(data.get("used_text_hashes", []))
        data["used_text_hashes"] = sorted(migrated)
        return data
    except Exception:
        return {"posted_slots": [], "recent_daily_hashes": [], "used_text_hashes": [], "history": []}


def prune_state(state, today):
    cutoff = today - timedelta(days=STATE_KEEP_DAYS)
    keep_dates = set()
    d = cutoff
    while d <= today:
        keep_dates.add(d.isoformat())
        d += timedelta(days=1)

    state["posted_slots"] = [
        s for s in state["posted_slots"]
        if s.split("T", 1)[0] in keep_dates
    ]
    state["history"] = [
        h for h in state["history"]
        if h.get("date") in keep_dates
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
            "topic_tag": "子育てママ",
            "access_token": ACCESS_TOKEN,
        },
    )
    if not result.get("id"):
        raise RuntimeError(f"Post ID was not returned: {result}")
    return result["id"]


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def used_hashes(state):
    # Permanent no-reuse policy: once a text hash is used, it stays blocked forever.
    return set(state.get("used_text_hashes", []))


def pick_daily_post(state, day, hour, minute):
    used = used_hashes(state)
    candidates = [p for p in DAILY_POSTS if text_hash(p) not in used]
    if not candidates:
        return None

    rng = seeded_rng(f"daily-text-v5|{day.isoformat()}|{hour:02d}:{minute:02d}")
    return rng.choice(candidates)


def pick_promo(kind, state, day, hour, minute):
    bank = AFFILIATE_PROMOS if kind == "affiliate" else SITE_PROMOS
    used = used_hashes(state)
    candidates = [p for p in bank if text_hash(p) not in used]
    if not candidates:
        return None
    rng = seeded_rng(f"promo-text-v5|{kind}|{day.isoformat()}|{hour:02d}:{minute:02d}")
    return rng.choice(candidates)


def last_actual_post_time(state):
    for item in reversed(state["history"]):
        ts = item.get("posted_at")
        if not ts:
            continue
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            continue
    return None


def next_due_slot(now, state):
    schedule = daily_schedule(now.date())
    posted = set(state["posted_slots"])
    due = []

    for h, m in schedule:
        planned = datetime(now.year, now.month, now.day, h, m, tzinfo=JST)
        key = f"{now.date().isoformat()}T{h:02d}:{m:02d}"
        if planned <= now and key not in posted:
            due.append((planned, h, m, key))

    if not due:
        return None

    last_post = last_actual_post_time(state)
    if last_post is not None and now - last_post < timedelta(minutes=ACTUAL_MIN_GAP_MINUTES):
        wait = timedelta(minutes=ACTUAL_MIN_GAP_MINUTES) - (now - last_post)
        print(f"Due post exists, but actual gap is too short. Retry in about {int(wait.total_seconds() // 60) + 1} min.")
        return None

    # Oldest due slot first. If GitHub Actions was delayed, the bot catches up
    # gradually rather than dropping the day's first post or publishing a burst.
    return due[0]


def main():
    now = datetime.now(JST)
    day = now.date()
    schedule = daily_schedule(day)

    print("JST now:", now.isoformat(timespec="minutes"))
    print("Today's planned slots:", ", ".join(f"{h:02d}:{m:02d}" for h, m in schedule))

    state = load_state()
    prune_state(state, day)

    due = next_due_slot(now, state)
    if due is None:
        print("No post is due right now.")
        return

    planned, hour, minute, slot_key = due
    print(f"Posting due slot {hour:02d}:{minute:02d} (runner time {now.strftime('%H:%M')}).")

    promo_plan = promo_plan_for_week(day)
    kind = promo_plan.get((day.isoformat(), hour, minute), "daily")

    if kind == "daily":
        text = pick_daily_post(state, day, hour, minute)
    else:
        text = pick_promo(kind, state, day, hour, minute)

    if text is None:
        print(f"No unused {kind} text remains. Skipping this slot rather than reusing old copy.")
        state["posted_slots"].append(slot_key)
        STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return

    post_id = publish(text)
    print(f"Published {kind} post: {post_id}")

    state["posted_slots"].append(slot_key)
    used = text_hash(text)
    state["used_text_hashes"].append(used)
    state["used_text_hashes"] = sorted(set(state["used_text_hashes"]))
    if kind == "daily":
        state["recent_daily_hashes"].append(used)
        state["recent_daily_hashes"] = state["recent_daily_hashes"][-RECENT_DAILY_LIMIT:]

    state["history"].append({
        "date": day.isoformat(),
        "planned_time": f"{hour:02d}:{minute:02d}",
        "posted_at": now.isoformat(timespec="seconds"),
        "kind": kind,
        "post_id": post_id,
        "text_hash": text_hash(text),
    })
    state["history"] = state["history"][-500:]
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
