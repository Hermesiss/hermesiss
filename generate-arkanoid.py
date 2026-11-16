#!/usr/bin/env python3
"""Generate a self-playing Arkanoid SVG from a GitHub contribution calendar.

Visual/HP model aligned with https://trismegistus.tech/apps/gh-arkanoid/
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# Dark → light (same order as gh-arkanoid GREEN_STEPS_DARK_TO_LIGHT)
GREEN_STEPS = ["#216e39", "#30a14e", "#40c463", "#9be9a8"]

LEVEL_HITS = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}

BG = "#0d1117"
PADDLE_COLOR = "#6e7681"
BALL_COLOR = "#f0f6fc"
TEXT_COLOR = "#c9d1d9"
ACCENT = "#238636"

CELL = 11
GAP = 3
PAD = 16
TOP = 28
PLAY_EXTRA = 100
PADDLE_W = 72
PADDLE_H = 8
BALL_R = 5
DT = 1 / 60
SUBSTEPS = 3
SAMPLE_FPS = 12
MAX_SECONDS = 90.0
BALL_SPEED = 220.0
PADDLE_SPEED = 400.0
# SVG teaser: 1 HP keeps the loop short; colors still encode contribution level.
# Interactive multi-hit lives in gh-arkanoid.
SVG_ONE_HIT = True

PLAY_URL = "https://trismegistus.tech/apps/gh-arkanoid/?u={login}"

GRAPHQL_QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        weeks {
          contributionDays {
            contributionCount
            contributionLevel
            date
            weekday
          }
        }
      }
    }
  }
}
"""


@dataclass
class Brick:
    col: int
    row: int
    x: float
    y: float
    w: float
    h: float
    shade_hits: int  # contribution intensity 1..4 (color)
    max_hits: int  # combat HP
    hits_left: int
    alive: bool = True
    hit_times: list[float] = field(default_factory=list)
    destroy_t: float | None = None

    def color_for_hits(self, hits: int) -> str:
        # more hits remaining → darker (index 0)
        idx = max(0, min(3, 4 - max(1, min(4, hits))))
        return GREEN_STEPS[idx]

    def initial_color(self) -> str:
        return self.color_for_hits(self.shade_hits)


@dataclass
class SimResult:
    bricks: list[Brick]
    ball_samples: list[tuple[float, float, float]]
    paddle_samples: list[tuple[float, float]]
    duration: float
    weeks: int
    days: int
    width: float
    height: float
    paddle_y: float


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--user",
        default=os.environ.get("ARKANOID_USER")
        or (os.environ.get("GITHUB_REPOSITORY", "Hermesiss/hermesiss").split("/")[0]),
        help="GitHub login (default: repo owner / ARKANOID_USER)",
    )
    p.add_argument("--fixture", help="JSON fixture with weeks[] (skip API)")
    p.add_argument("-o", "--output", default="arkanoid.svg")
    p.add_argument("--token", default=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
    return p.parse_args()


def fetch_calendar_graphql(login: str, token: str | None) -> list[dict[str, Any]]:
    body = json.dumps({"query": GRAPHQL_QUERY, "variables": {"login": login}}).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "generate-arkanoid",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        "https://api.github.com/graphql", data=body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise SystemExit(f"GitHub API error {e.code}: {detail}") from e
    if payload.get("errors"):
        raise SystemExit(f"GraphQL errors: {payload['errors']}")
    user = payload.get("data", {}).get("user")
    if not user:
        raise SystemExit(f"User not found: {login}")
    return user["contributionsCollection"]["contributionCalendar"]["weeks"]


def fetch_calendar_jogruber(login: str) -> list[dict[str, Any]]:
    """Fallback used by gh-arkanoid (no token)."""
    url = f"https://github-contributions-api.jogruber.de/v4/{login}?y=last"
    req = urllib.request.Request(url, headers={"User-Agent": "generate-arkanoid", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    contribs = data.get("contributions") or []
    if not contribs:
        raise SystemExit("jogruber: empty contributions")

    # Build weeks Sun→Sat like GitHub calendar
    from datetime import datetime, timedelta, timezone

    def parse_day(s: str) -> datetime:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    daily = sorted(
        (
            {
                "date": c["date"],
                "count": int(c.get("count") or 0),
                "level": int(c["level"]) if c.get("level") is not None else 0,
                "dt": parse_day(c["date"]),
            }
            for c in contribs
            if c.get("date")
        ),
        key=lambda x: x["dt"],
    )
    last = min(datetime.now(timezone.utc), daily[-1]["dt"])
    start = last - timedelta(days=364)
    daily = [d for d in daily if start <= d["dt"] <= last]
    if not daily:
        raise SystemExit("jogruber: no days in window")

    first = daily[0]["dt"]
    cursor = first - timedelta(days=first.weekday() % 7)  # align... GitHub uses Sunday=0
    # Python weekday: Mon=0; GitHub weekday: Sun=0
    # Align to previous Sunday:
    cursor = first - timedelta(days=(first.weekday() + 1) % 7)

    by_date = {d["date"]: d for d in daily}
    level_names = ["NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"]
    weeks: list[dict[str, Any]] = []
    end = daily[-1]["dt"]
    current: list[dict[str, Any]] = []
    while cursor <= end + timedelta(days=6):
        key = cursor.strftime("%Y-%m-%d")
        d = by_date.get(key)
        count = d["count"] if d else 0
        level_i = d["level"] if d else 0
        if count <= 0:
            level_i = 0
        level_i = max(0, min(4, level_i))
        current.append(
            {
                "contributionCount": count,
                "contributionLevel": level_names[level_i],
                "weekday": (cursor.weekday() + 1) % 7,  # Sun=0
                "date": key,
            }
        )
        if len(current) == 7:
            weeks.append({"contributionDays": current})
            current = []
        cursor += timedelta(days=1)
    if current:
        while len(current) < 7:
            current.append(
                {
                    "contributionCount": 0,
                    "contributionLevel": "NONE",
                    "weekday": len(current),
                    "date": "",
                }
            )
        weeks.append({"contributionDays": current})
    return weeks[-53:]


def load_fixture(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "weeks" in data:
        return data["weeks"]
    if isinstance(data, list):
        return data
    raise SystemExit("Fixture must be a weeks list or {weeks: [...]}")


def build_bricks(weeks: list[dict[str, Any]]) -> tuple[list[Brick], int, int, float, float]:
    bricks: list[Brick] = []
    n_weeks = len(weeks)
    n_days = 7
    for col, week in enumerate(weeks):
        days = week.get("contributionDays") or []
        for day in days:
            count = int(day.get("contributionCount") or 0)
            level = day.get("contributionLevel") or "NONE"
            level_hits = LEVEL_HITS.get(level, 0)
            if count <= 0 or level_hits <= 0:
                continue
            combat_hits = 1 if SVG_ONE_HIT else level_hits
            weekday = int(day.get("weekday", 0))
            x = PAD + col * (CELL + GAP)
            y = TOP + weekday * (CELL + GAP)
            bricks.append(
                Brick(
                    col=col,
                    row=weekday,
                    x=x,
                    y=y,
                    w=CELL,
                    h=CELL,
                    shade_hits=level_hits,
                    max_hits=combat_hits,
                    hits_left=combat_hits,
                )
            )
    grid_w = PAD * 2 + max(n_weeks, 1) * CELL + max(n_weeks - 1, 0) * GAP
    grid_h = TOP + n_days * CELL + (n_days - 1) * GAP
    return bricks, n_weeks, n_days, grid_w, grid_h


def collide_side(
    bx: float, by: float, br: float, rx: float, ry: float, rw: float, rh: float
) -> str | None:
    nearest_x = max(rx, min(bx, rx + rw))
    nearest_y = max(ry, min(by, ry + rh))
    dx = bx - nearest_x
    dy = by - nearest_y
    if dx * dx + dy * dy > br * br:
        return None
    overlap_l = bx + br - rx
    overlap_r = rx + rw - (bx - br)
    overlap_t = by + br - ry
    overlap_b = ry + rh - (by - br)
    sides = [("left", overlap_l), ("right", overlap_r), ("top", overlap_t), ("bottom", overlap_b)]
    sides.sort(key=lambda s: s[1])
    return sides[0][0]


def pick_target_brick(bricks: list[Brick], ball_x: float, ball_y: float | None = None) -> Brick | None:
    """Weighted random target — penalize near-vertical so shots stay interesting."""
    alive = [b for b in bricks if b.alive]
    if not alive:
        return None
    by = ball_y if ball_y is not None else max(b.y + b.h for b in alive) + 80.0

    weights: list[float] = []
    for b in alive:
        cx = b.x + b.w / 2
        cy = b.y + b.h / 2
        dx = abs(cx - ball_x)
        dy = max(by - cy, 1.0)
        ang = abs(math.atan2(dx, dy))  # 0 = straight up
        # sweet spot ~20–55° from vertical; dump pure vertical & extreme skimmers
        if ang < math.radians(12):
            w = 0.15
        elif ang < math.radians(55):
            w = 1.0 + (ang / math.radians(55))
        elif ang < math.radians(75):
            w = 0.7
        else:
            w = 0.25
        # slight bias to lower / closer bricks
        w *= 1.0 + 0.35 * ((b.y + b.h) / max(by, 1.0))
        span = max((br.x + br.w for br in alive), default=400.0)
        w *= 1.0 / (1.0 + dx / span)
        weights.append(max(w, 0.05))

    return random.choices(alive, weights=weights, k=1)[0]


def aim_at_brick(
    ball_x: float, ball_y: float, brick: Brick, speed: float, width: float
) -> tuple[float, float]:
    """Launch toward brick with small aim jitter (not mid-air homing)."""
    tx = brick.x + brick.w / 2 + random.uniform(-brick.w * 0.35, brick.w * 0.35)
    ty = brick.y + brick.h / 2 + random.uniform(-brick.h * 0.25, brick.h * 0.25)
    speed = max(speed, BALL_SPEED * 0.9) * random.uniform(0.92, 1.08)
    dx = tx - ball_x
    dy = ty - ball_y
    if dy >= -1:
        dy = -60.0
    # if still too vertical, kick sideways
    if abs(dx) < 18:
        dx += random.choice((-1, 1)) * random.uniform(40, 120)
    dist = math.hypot(dx, dy) or 1.0
    return dx / dist * speed, dy / dist * speed


def paddle_x_for_launch(ball_x: float, vx: float, vy: float, width: float) -> float:
    """Offset paddle under the ball so hit position matches launch angle (looks intentional)."""
    ang = math.atan2(vx, max(-vy, 1e-6))
    offset = max(-1.0, min(1.0, ang / (math.pi / 2)))
    px = ball_x - PADDLE_W / 2 - offset * (PADDLE_W / 2) * 0.9
    return max(0.0, min(width - PADDLE_W, px))


def paddle_launch(
    ball_x: float, ball_y: float, bricks: list[Brick], speed: float, width: float
) -> tuple[float, float, float]:
    """Returns vx, vy, paddle_x for an aimed paddle bounce."""
    target = pick_target_brick(bricks, ball_x, ball_y)
    if not target:
        ang = random.uniform(-0.7, 0.7)
        return math.sin(ang) * speed, -abs(math.cos(ang)) * speed, max(
            0.0, min(width - PADDLE_W, ball_x - PADDLE_W / 2)
        )
    vx, vy = aim_at_brick(ball_x, ball_y, target, speed, width)
    px = paddle_x_for_launch(ball_x, vx, vy, width)
    return vx, vy, px


def simulate(bricks: list[Brick], width: float, grid_h: float) -> SimResult:
    height = grid_h + PLAY_EXTRA
    paddle_y = height - PAD - PADDLE_H - 18
    floor_y = height - PAD

    paddle_x = width / 2 - PADDLE_W / 2
    ball_x = width / 2
    ball_y = paddle_y - BALL_R - 2
    vx, vy, paddle_x = paddle_launch(ball_x, ball_y, bricks, BALL_SPEED, width)

    ball_samples: list[tuple[float, float, float]] = [(0.0, ball_x, ball_y)]
    paddle_samples: list[tuple[float, float]] = [(0.0, paddle_x)]
    sample_every = 1.0 / SAMPLE_FPS
    next_sample = sample_every

    t = 0.0
    alive_count = sum(1 for b in bricks if b.alive)

    while t < MAX_SECONDS and alive_count > 0:
        # Only chase the ball to catch it — aim happens on contact
        if vy > 0:
            eta = max(0.0, (paddle_y - ball_y) / max(vy, 1e-6))
            intercept_x = ball_x + vx * eta
            # reflect once off side walls for better intercept
            if intercept_x < 0:
                intercept_x = -intercept_x
            elif intercept_x > width:
                intercept_x = 2 * width - intercept_x
            intercept_x = max(BALL_R, min(width - BALL_R, intercept_x))
            want = intercept_x - PADDLE_W / 2
        else:
            want = ball_x - PADDLE_W / 2
        dx = want - paddle_x
        max_step = PADDLE_SPEED * DT
        if dx:
            paddle_x += math.copysign(min(abs(dx), max_step), dx)
        paddle_x = max(0.0, min(width - PADDLE_W, paddle_x))

        hit_this_frame = False
        step = DT / SUBSTEPS
        for _ in range(SUBSTEPS):
            ball_x += vx * step
            ball_y += vy * step

            if ball_x - BALL_R < 0:
                ball_x = BALL_R
                vx = abs(vx)
            elif ball_x + BALL_R > width:
                ball_x = width - BALL_R
                vx = -abs(vx)
            if ball_y - BALL_R < 0:
                ball_y = BALL_R
                vy = abs(vy)

            if ball_y + BALL_R > floor_y:
                ball_y = paddle_y - BALL_R
                speed = max(math.hypot(vx, vy), BALL_SPEED)
                vx, vy, paddle_x = paddle_launch(ball_x, ball_y, bricks, speed, width)
                hit_this_frame = True
                break

            if vy > 0 and collide_side(ball_x, ball_y, BALL_R, paddle_x, paddle_y, PADDLE_W, PADDLE_H):
                ball_y = paddle_y - BALL_R - 1
                speed = max(math.hypot(vx, vy), BALL_SPEED)
                vx, vy, paddle_x = paddle_launch(ball_x, ball_y, bricks, speed, width)
                hit_this_frame = True
                break

            for b in bricks:
                if not b.alive:
                    continue
                side = collide_side(ball_x, ball_y, BALL_R, b.x, b.y, b.w, b.h)
                if not side:
                    continue
                # Normal geometric bounce only — no mid-air retarget
                if side == "left":
                    ball_x = b.x - BALL_R - 0.5
                    vx = -abs(vx)
                elif side == "right":
                    ball_x = b.x + b.w + BALL_R + 0.5
                    vx = abs(vx)
                elif side == "top":
                    ball_y = b.y - BALL_R - 0.5
                    vy = -abs(vy)
                else:
                    ball_y = b.y + b.h + BALL_R + 0.5
                    vy = abs(vy)

                b.hits_left -= 1
                b.hit_times.append(t)
                if b.hits_left <= 0:
                    b.alive = False
                    b.destroy_t = t
                    alive_count -= 1
                hit_this_frame = True
                break
            if hit_this_frame:
                break

        t += DT
        if t >= next_sample or alive_count == 0:
            ball_samples.append((t, ball_x, ball_y))
            paddle_samples.append((t, paddle_x))
            next_sample = t + sample_every

    if ball_samples[-1][0] < t:
        ball_samples.append((t, ball_x, ball_y))
        paddle_samples.append((t, paddle_x))

    duration = min(max(t, 1.0) + 0.8, MAX_SECONDS + 0.8)
    ball_samples.append((duration, ball_samples[-1][1], ball_samples[-1][2]))
    paddle_samples.append((duration, paddle_samples[-1][1]))

    return SimResult(
        bricks=bricks,
        ball_samples=ball_samples,
        paddle_samples=paddle_samples,
        duration=duration,
        weeks=0,
        days=7,
        width=width,
        height=height,
        paddle_y=paddle_y,
    )


def _anim_values(times: list[float], values: list[float], duration: float) -> tuple[str, str, str]:
    if duration <= 0:
        duration = 1.0
    # dedupe identical consecutive keyTimes (SMIL requires strictly increasing for some viewers)
    kt_parts: list[str] = []
    val_parts: list[str] = []
    last = -1.0
    for t, v in zip(times, values):
        kt = max(0.0, min(1.0, t / duration))
        if kt <= last:
            kt = min(1.0, last + 0.0001)
        last = kt
        kt_parts.append(f"{kt:.4f}")
        val_parts.append(f"{v:.2f}")
    if kt_parts[-1] != "1.0000":
        kt_parts.append("1.0000")
        val_parts.append(val_parts[-1])
    return ";".join(kt_parts), ";".join(val_parts), f"{duration:.3f}s"


def _brick_fill_anim(b: Brick, dur: float) -> str:
    """Optional shade steps on multi-hit, then hide via opacity."""
    parts: list[str] = []
    if not SVG_ONE_HIT and b.hit_times:
        events: list[tuple[float, str]] = [(0.0, b.color_for_hits(b.shade_hits))]
        shade = b.shade_hits
        for ht in b.hit_times:
            shade = max(1, shade - 1)
            if b.destroy_t is not None and ht >= b.destroy_t:
                break
            events.append((ht, b.color_for_hits(shade)))
        times = [e[0] for e in events]
        colors = [e[1] for e in events]
        if times[-1] < dur:
            times.append(dur)
            colors.append(colors[-1])
        kt_parts: list[str] = []
        last = -1.0
        for t in times:
            kt = max(0.0, min(1.0, t / dur))
            if kt <= last:
                kt = min(1.0, last + 0.0001)
            last = kt
            kt_parts.append(f"{kt:.4f}")
        if kt_parts[-1] != "1.0000":
            kt_parts.append("1.0000")
            colors.append(colors[-1])
        parts.append(
            f'<animate attributeName="fill" values="{";".join(colors)}" '
            f'keyTimes="{";".join(kt_parts)}" dur="{dur:.3f}s" '
            f'repeatCount="indefinite" calcMode="discrete"/>'
        )

    if b.destroy_t is None:
        return "".join(parts)

    t0 = max(0.0, min(0.998, b.destroy_t / dur))
    t1 = min(1.0, t0 + 0.001)
    parts.append(
        f'<animate attributeName="opacity" values="1;1;0;0" '
        f'keyTimes="0;{t0:.4f};{t1:.4f};1" dur="{dur:.3f}s" '
        f'repeatCount="indefinite" calcMode="linear"/>'
    )
    return "".join(parts)


def emit_svg(sim: SimResult, login: str) -> str:
    dur = sim.duration
    bt, bx, by = zip(*sim.ball_samples)
    pt, px = zip(*sim.paddle_samples)
    b_kt, b_vx, b_dur = _anim_values(list(bt), list(bx), dur)
    _, b_vy, _ = _anim_values(list(bt), list(by), dur)
    p_kt, p_vx, p_dur = _anim_values(list(pt), list(px), dur)
    play = PLAY_URL.format(login=login.lower())

    lines: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{sim.width:.0f}" height="{sim.height:.0f}" '
        f'viewBox="0 0 {sim.width:.0f} {sim.height:.0f}" role="img" '
        f'aria-label="Contribution Arkanoid for {login}">',
        f"<title>Contribution Arkanoid — @{login} (click to play)</title>",
        f'<a href="{play}" target="_blank" rel="noopener noreferrer">',
        f'<rect width="100%" height="100%" fill="{BG}"/>',
        f'<text x="{PAD}" y="18" fill="{TEXT_COLOR}" font-size="12" '
        f'font-family="system-ui,-apple-system,Segoe UI,Helvetica,Arial,sans-serif">'
        f"@{login}</text>",
        f'<text x="{sim.width - PAD}" y="18" fill="{ACCENT}" font-size="11" text-anchor="end" '
        f'font-family="system-ui,-apple-system,Segoe UI,Helvetica,Arial,sans-serif">'
        f"play ↗</text>",
    ]

    for b in sim.bricks:
        lines.append(
            f'<rect x="{b.x:.1f}" y="{b.y:.1f}" width="{b.w}" height="{b.h}" rx="2" ry="2" '
            f'fill="{b.initial_color()}">'
        )
        lines.append(_brick_fill_anim(b, dur))
        lines.append("</rect>")

    lines.append(
        f'<rect x="{px[0]:.2f}" y="{sim.paddle_y:.1f}" '
        f'width="{PADDLE_W}" height="{PADDLE_H}" rx="3" fill="{PADDLE_COLOR}">'
    )
    lines.append(
        f'<animate attributeName="x" values="{p_vx}" keyTimes="{p_kt}" '
        f'dur="{p_dur}" repeatCount="indefinite" calcMode="linear"/>'
    )
    lines.append("</rect>")

    lines.append(f'<circle cx="{bx[0]:.2f}" cy="{by[0]:.2f}" r="{BALL_R}" fill="{BALL_COLOR}">')
    lines.append(
        f'<animate attributeName="cx" values="{b_vx}" keyTimes="{b_kt}" '
        f'dur="{b_dur}" repeatCount="indefinite" calcMode="linear"/>'
    )
    lines.append(
        f'<animate attributeName="cy" values="{b_vy}" keyTimes="{b_kt}" '
        f'dur="{b_dur}" repeatCount="indefinite" calcMode="linear"/>'
    )
    lines.append("</circle>")
    lines.append("</a>")
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def make_demo_weeks() -> list[dict[str, Any]]:
    weeks = []
    levels = [
        "NONE",
        "FIRST_QUARTILE",
        "SECOND_QUARTILE",
        "THIRD_QUARTILE",
        "FOURTH_QUARTILE",
    ]
    for w in range(20):
        days = []
        for d in range(7):
            idx = (w + d * 2) % 5
            if w < 2 or w > 17:
                idx = 0
            elif (w + d) % 3 == 0:
                idx = min(4, idx + 1)
            level = levels[idx]
            days.append(
                {
                    "contributionCount": 0 if level == "NONE" else idx,
                    "contributionLevel": level,
                    "weekday": d,
                    "date": f"2025-01-{(w * 7 + d) % 28 + 1:02d}",
                }
            )
        weeks.append({"contributionDays": days})
    return weeks


def main() -> None:
    args = parse_args()
    if args.fixture:
        weeks = load_fixture(args.fixture)
    elif args.token:
        weeks = fetch_calendar_graphql(args.user, args.token)
    else:
        try:
            print("no token; trying jogruber API…", file=sys.stderr)
            weeks = fetch_calendar_jogruber(args.user)
        except Exception as e:
            print(f"jogruber failed ({e}); using demo calendar", file=sys.stderr)
            weeks = make_demo_weeks()

    bricks, n_weeks, n_days, grid_w, grid_h = build_bricks(weeks)
    if not bricks:
        weeks = make_demo_weeks()
        bricks, n_weeks, n_days, grid_w, grid_h = build_bricks(weeks)

    # Stable-ish daily variety (override with ARKANOID_SEED)
    seed = os.environ.get("ARKANOID_SEED") or f"{args.user}:{n_weeks}:{len(bricks)}"
    random.seed(seed)

    sim = simulate(bricks, grid_w, grid_h)
    sim.weeks = n_weeks
    sim.days = n_days
    svg = emit_svg(sim, args.user)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"wrote {args.output} ({len(bricks)} bricks, {sim.duration:.1f}s, {n_weeks} weeks)")


if __name__ == "__main__":
    main()
