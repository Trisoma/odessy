# Local usage history

The coordinator records metadata for cloud calls when the provider returns usage.
Each record has a UTC time, configured participant/provider/model, reported token
counts and an estimated cost only when configured prices and required token fields
are available. No prompts, responses, tool payloads or credentials are stored.
Unreported fields remain unknown. Local models, requests without usage data, failed
requests without reports and external coding CLI usage are not counted. A retry is
represented only if its provider response reports usage. Figures are not a bill.

The 7-, 30- and 90-day filters cover that many UTC calendar days, including today.
Days without recorded requests are shown as gaps; they do not establish that no
untracked usage occurred. Estimated cost is unknown when prices were not configured
at the time of a call or required usage data was missing. Configuring prices later
does not retroactively price earlier records, and a mixed or incomplete set does
not produce a complete estimated total.

The database is `usage-history.sqlite3` beside the file named by
`ODESSY_CONFIG_FILE`, or under the current user's `.config/odessy` otherwise.
It persists across sessions and service restarts, is not automatically purged, and
is not backfilled from earlier conversations. On POSIX new database files are
created with user-only permissions; on Windows they inherit the user's directory
permissions. The application does not currently provide export or deletion UI.
To remove the history, stop the coordinator and remove this database file; do not
remove configuration or credentials. A subsequent coordinator creates an empty
database. Back it up separately if retention is desired.

Failures to write history never block model operation. They are logged without the
underlying error and flagged on the usage page for the current service process;
the failed records cannot be recovered automatically. SQLite access errors are
reported as unavailable through the authenticated API. The database belongs to
the service's configuration, and connected clients see that service's history,
not an independently inferred session total.

The `--usage-preview` desktop flag uses fictional in-memory fixture data. It does
not access the usage database or send requests to models, tools or a service.
