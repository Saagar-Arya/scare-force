"""Recover the stored detection data: one zip with the photos, the CSVs and an HTML report.

    python -m scare.export                          # everything logged so far
    python -m scare.export --from 2026-11-20 --to 2026-11-24
    python -m scare.export --out /media/usb/farm.zip   # straight onto a USB stick

The zip (default logs/exports/scare-export_<from>_<to>.zip) holds:
    report.html       the user report (Challenge 1): detections per day and species, scares, and
                      every detection with its time, camera, species, confidence, outcome and
                      annotated photo (click for full size and the raw frame)
    detections.csv    log.detections rows in the range (columns: scare/detection_log.py)
    activations.csv   log.activations rows in the range (columns: scare/activation_log.py)
    photos/<date>/    each detection's raw and annotated JPEG
Unzip it and open report.html; every link is relative, so it works offline on any computer.
Times are the Pi's clock: the report says so and shows the UTC offset.
"""

import argparse
import csv
import html
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from . import ROOT, load_config


def read_rows(path: Path, time_key: str, start: date | None, end: date | None) -> list[dict]:
    """Rows of a log CSV (and nothing if it doesn't exist) whose day is in [start, end]."""
    if not path.exists():
        return []
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        try:
            day = datetime.fromisoformat(r[time_key]).date()
        except (KeyError, ValueError):
            continue
        if (start is None or day >= start) and (end is None or day <= end):
            out.append(r)
    return out


def write_csv(rows: list[dict], fields) -> str:
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


CSS = """
:root { --bg:#fff; --fg:#1b1b1b; --muted:#666; --line:#ddd; --card:#f6f6f4; --hit:#b42318; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#161616; --fg:#eee; --muted:#9a9a9a; --line:#333; --card:#1f1f1f; --hit:#f97066; }
}
body { background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; margin:0;
       padding:24px 16px; max-width:1100px; margin:auto; }
h1 { font-size:22px; margin:0 0 4px } h2 { font-size:18px; margin:28px 0 8px }
.muted { color:var(--muted) } table { border-collapse:collapse; margin:8px 0 }
th, td { border-bottom:1px solid var(--line); padding:4px 10px; text-align:left }
td.n, th.n { text-align:right; font-variant-numeric:tabular-nums }
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(250px, 1fr)); gap:12px }
.card { background:var(--card); border-radius:8px; overflow:hidden }
.card img { width:100%; display:block } .card div { padding:6px 10px; font-size:13px }
.scare { color:var(--hit); font-weight:600 }
"""


def build_report(dets: list[dict], acts: list[dict], start: str, end: str) -> str:
    e = html.escape
    species = sorted({r["label"] for r in dets})
    by_day = defaultdict(list)
    for r in dets:
        by_day[r["time"][:10]].append(r)
    scares = [r for r in dets if r["outcome"] == "scare"]
    act_by_id = {a.get("detection_id"): a for a in acts if a.get("detection_id")}
    out = [f"<!doctype html><html lang=en><head><meta charset=utf-8>"
           f"<meta name=viewport content='width=device-width, initial-scale=1'>"
           f"<title>Scare Force detections</title><style>{CSS}</style></head><body>",
           "<h1>Scare Force detection report</h1>",
           f"<p class=muted>{e(start)} to {e(end)} &middot; generated "
           f"{datetime.now().astimezone():%Y-%m-%d %H:%M %Z} &middot; times are the Pi's clock "
           f"(UTC offset shown in detections.csv)</p>",
           f"<p><b>{len(dets)}</b> confirmed detections, <b>{len(scares)}</b> fired a scare; "
           f"{len(acts)} scares logged in total (including manual tests).</p>"]
    if not dets:
        out.append("<p>No detections in this range.</p></body></html>")
        return "\n".join(out)

    out.append("<h2>Per day and species</h2><table><tr><th>Day</th>"
               + "".join(f"<th class=n>{e(s)}</th>" for s in species)
               + "<th class=n>total</th><th class=n>scares</th><th>first</th><th>last</th></tr>")
    for day in sorted(by_day):
        rows = by_day[day]
        c = Counter(r["label"] for r in rows)
        times = sorted(r["time"][11:19] for r in rows)
        out.append(f"<tr><td>{e(day)}</td>" + "".join(f"<td class=n>{c[s]}</td>" for s in species)
                   + f"<td class=n>{len(rows)}</td>"
                   + f"<td class=n>{sum(r['outcome'] == 'scare' for r in rows)}</td>"
                   + f"<td>{times[0]}</td><td>{times[-1]}</td></tr>")
    c = Counter(r["label"] for r in dets)
    out.append("<tr><th>all</th>" + "".join(f"<th class=n>{c[s]}</th>" for s in species)
               + f"<th class=n>{len(dets)}</th><th class=n>{len(scares)}</th><th></th><th></th>"
               "</tr></table>")

    cams = Counter(r["camera_name"] or r["camera"] for r in dets)
    out.append("<h2>Per camera</h2><table><tr><th>Camera</th><th class=n>detections</th></tr>"
               + "".join(f"<tr><td>{e(k)}</td><td class=n>{v}</td></tr>" for k, v in cams.items())
               + "</table>")

    for day in sorted(by_day):
        out.append(f"<h2>{e(day)}</h2><div class=grid>")
        for r in sorted(by_day[day], key=lambda r: r["time"]):
            img = (f"<a href='photos/{e(r['annotated'])}'><img loading=lazy "
                   f"src='photos/{e(r['annotated'])}' alt='{e(r['label'])}'></a>"
                   if r["annotated"] else "<div class=muted>(no photo saved)</div>")
            act = act_by_id.get(r["detection_id"])
            scare = ""
            if r["outcome"] == "scare":
                scare = "<span class=scare>scare</span>"
                if act:
                    scare += (f" {e(act.get('scare_s', ''))} s, {e(act.get('light_pattern', ''))}"
                              f" light, {e(act.get('sound_type', ''))}")
            else:
                scare = e(r["outcome"])
            raw = f" &middot; <a href='photos/{e(r['photo'])}'>raw</a>" if r["photo"] else ""
            out.append(f"<div class=card>{img}<div><b>{e(r['label'])}</b> "
                       f"{float(r['confidence']):.2f} &middot; {e(r['time'][11:19])} &middot; "
                       f"{e(r['camera_name'] or r['camera'])}<br>{scare}{raw}</div></div>")
        out.append("</div>")
    out.append("</body></html>")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Zip the detection photos, CSVs and an HTML report")
    ap.add_argument("--from", dest="start", type=date.fromisoformat, help="first day, YYYY-MM-DD")
    ap.add_argument("--to", dest="end", type=date.fromisoformat, help="last day, YYYY-MM-DD")
    ap.add_argument("--out", type=Path, help="zip path (default logs/exports/...)")
    args = ap.parse_args()
    log = load_config()["log"]
    if not log.get("detections"):
        raise SystemExit("log.detections is null in config.yaml: nothing to export")
    from .activation_log import FIELDS as ACT_FIELDS
    from .detection_log import FIELDS as DET_FIELDS

    dets = read_rows(ROOT / log["detections"], "time", args.start, args.end)
    acts = read_rows(ROOT / log["activations"], "start", args.start, args.end) \
        if log.get("activations") else []
    days = sorted({r["time"][:10] for r in dets})
    start = args.start.isoformat() if args.start else (days[0] if days else "start")
    end = args.end.isoformat() if args.end else (days[-1] if days else date.today().isoformat())
    out = args.out or ROOT / "logs" / "exports" / f"scare-export_{start}_{end}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    photos = ROOT / log.get("photos", "logs/photos")
    missing = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.html", build_report(dets, acts, start, end))
        z.writestr("detections.csv", write_csv(dets, DET_FIELDS))
        z.writestr("activations.csv", write_csv(acts, ACT_FIELDS))
        for r in dets:
            for key in ("photo", "annotated"):
                if not r[key]:
                    continue
                src = photos / r[key]
                if src.exists():
                    z.write(src, f"photos/{r[key]}", compress_type=zipfile.ZIP_STORED)
                else:
                    missing += 1
    size = out.stat().st_size / 1e6
    print(f"[export] {len(dets)} detections ({sum(r['outcome'] == 'scare' for r in dets)} scares), "
          f"{len(acts)} activation rows, {start} to {end} -> {out} ({size:.1f} MB)"
          + (f"; {missing} photo file(s) missing" if missing else ""), flush=True)
    print("[export] copy it off with a USB stick (--out /media/<user>/<stick>/...) or scp; "
          "unzip and open report.html", flush=True)


if __name__ == "__main__":
    main()
