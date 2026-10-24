# Operations Handbook

## Backups

Nightly backups run at 02:00 UTC and are retained for thirty days. Backup media
is rotated quarterly, and each rotation is audited by the platform team.

## Incident response

Page the on-call engineer when the error rate exceeds 5% over a five-minute
window. Do not page for a single failed health check.

## Maintenance windows

Standard maintenance happens on Sundays between 04:00 and 06:00 UTC. Anything
touching the primary database needs a change advisory approval first.
