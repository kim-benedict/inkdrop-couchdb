# Scheduled notes

Run these commands from the checkout on your Debian server. The server storage guard must already be running. Scheduling uses systemd; your desktop can be off.

| Example | Schedule (Asia/Seoul) | Note title |
| --- | --- | --- |
| `daily-todo` | Every day at midnight | `2026-10-04` |
| `weekly-review` | Monday at 09:00 | `Review 2026-W41` |
| `monthly-plan` | First day of the month at 09:00 | `Plan 2026-11` |

Edit `job.json` to set the existing notebook name, time zone, calendar expression, title prefix, and daily/weekly/monthly grouping. Edit `template.md` for the initial checklist. `{{date}}` expands to the local creation date; `{{period}}` expands to the title's date, ISO week, or month.

```sh
python3 server/install_schedule.py examples/daily-todo/job.json --check
sudo python3 server/install_schedule.py examples/daily-todo/job.json
sudo systemctl list-timers inkdrop-daily-todo.timer
```

Install a different example by changing the path. Each installation creates one note immediately, then enables its timer. A matching title in the target notebook is left untouched. Use a unique job ID and title prefix for additional jobs; jobs sharing a title intentionally share that note.

The installer prompts for the existing `inkdrop` sync password on first use. Credentials and resolved notebook IDs stay in root-only `/etc/inkdrop/schedules/`. Reinstalling the same job updates its settings. Changed configuration is backed up privately under `/var/lib/inkdrop/schedule-backups/`; a failed installation restores it.

```sh
sudo journalctl -u inkdrop-daily-todo.service -n 10 --no-pager
sudo python3 server/install_schedule.py --uninstall daily-todo
```

Removal keeps notes and private configuration. Creation pauses when the storage guard is stale or unsafe. Retries never replace existing notes, and a deleted generated note is not recreated. After downtime, the persistent timer creates only the current period, without filling missed dates. Examples create template notes; they do not summarize or copy previous tasks.
