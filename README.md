# Free washing machine watcher 🧺

Checks Marktplaats every ~10 minutes for **free ("Gratis") washing machines**
within **10 km of Canvas Living Brainpark** (K.P. van der Mandelelaan 130,
3062 MB Rotterdam) and sends a push notification through **ntfy** for each new one.

Runs for free on GitHub Actions — no server needed.

## Setup (5 minutes)

1. **Install ntfy** on your phone ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iOS](https://apps.apple.com/app/ntfy/id1625396347)).
2. **Pick a secret topic name** — anyone who knows it can read your messages,
   so make it unguessable, e.g. `washer-rdam-8f3k2q9x`.
   In the app: **+ → Subscribe to topic →** enter that name.
3. **Create the GitHub repo** and push these files:
   ```bash
   git init && git add . && git commit -m "Initial commit"
   gh repo create marktplaats-free-washer --private --source=. --push
   ```
4. **Add the secret**: repo → *Settings → Secrets and variables → Actions →
   New repository secret* → name `NTFY_TOPIC`, value = your topic name.
5. **Run it once**: *Actions → Check free washing machines → Run workflow*.
   The first run only records what's already listed (no notification spam);
   from then on you get a ping for every new free washer.

Test your phone setup any time:
```bash
curl -d "test 🧺" ntfy.sh/YOUR_TOPIC
```

## Settings

All optional, set as environment variables (in the workflow's `env:` block):

| Variable | Default | Meaning |
|---|---|---|
| `POSTCODE` | `3062MB` | Center of the search (Canvas Brainpark) |
| `RADIUS_M` | `10000` | Radius in meters |
| `QUERIES` | `wasmachine,wasmachines,washing machine,was-droogcombinatie` | Comma-separated search terms |
| `NTFY_SERVER` | `https://ntfy.sh` | Self-hosted ntfy URL (repo *variable*) |
| `NTFY_TOKEN` | – | Access token for a protected topic (repo *secret*) |
| `NOTIFY_ON_FIRST_RUN` | – | Set to `1` to also notify for listings already online |

Filtering keywords (`EXCLUDE` for "gezocht", parts, repair services etc., and
`MUST_HAVE`) are at the top of `check.py`.

## How it works

- Calls Marktplaats' public search endpoint (`/lrp/api/search`) with the
  postcode + `distanceMeters=10000`, newest first.
- Keeps listings whose price type is **Gratis** (or a fixed price of €0).
- Drops "gezocht", parts and repair ads.
- **Skips defective or noisy machines.** For every new free listing it opens
  the listing page, reads the full description, and skips it if the seller
  mentions things like *defect, kapot, werkt niet, lekt, foutcode, lawaai,
  herrie, rammelt, piept, raar geluid, lager kapot*, or the English
  equivalents. Marktplaats' own condition field "Niet werkend" is also
  rejected. Negations are understood, so *"geen rare geluiden"*,
  *"niet defect"* and *"no leaks"* still pass. Skipped listings show up in
  the Actions log with the reason, for example
  `skipped (looks defective/noisy: 'herrie')`.
  The keyword list (`DEFECT_PATTERNS`) is at the top of `check.py`.
  A seller who doesn't mention a problem will still get through, so ask
  "werkt hij goed en maakt hij geen vreemde geluiden?" before picking it up.
- Item IDs already notified are stored in `seen.json`, which the workflow
  commits back to the repo.

## Notes

- GitHub's scheduler isn't exact; runs can be delayed during busy periods.
  Free washers go fast, so if you want near-instant alerts, run `check.py`
  from a cron job on a Raspberry Pi / always-on machine instead:
  `*/5 * * * * cd /path/to/repo && NTFY_TOPIC=... python3 check.py`
- GitHub disables scheduled workflows after 60 days with no repo activity.
  The `seen.json` commits normally keep it alive; if it ever stops, just
  re-enable it in the Actions tab.
- This uses an unofficial endpoint; if Marktplaats changes it, the script
  will log a warning and need a small update.
