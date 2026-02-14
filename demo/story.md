# A Week with the Orchestrator

You're a staff engineer. You're the tech lead on a service decomposition that's been on the roadmap for two quarters. You're the DRI for a logging migration that security flagged as urgent. You have a backlog of tech debt you keep promising the team you'll get to. And every week, something in production breaks and you're the one who knows the system well enough to fix it fast.

You can't write all the code yourself. But you're the person who knows what the right approach is, where the landmines are, and what "done" actually looks like for each of these. That's the bottleneck — not the typing, but the fact that there's only one of you.

---

## Monday morning

You open the dashboard. You have three projects:

**Notifications Service Extraction** — your main initiative. You're pulling the notifications module out of the monolith into its own service. It touches the user preferences store, the email/push delivery pipeline, and a dozen API consumers that currently import it as a library. You've done the hard design work already. The API contract is defined, the data migration plan is written. Now it's execution — and there's a lot of it.

**Structured Logging Migration** — security wants log4j replaced with structured logging across all your team's services. Eight repos. Most of them are straightforward find-and-replace, but a few have custom appenders that need actual thought.

**Backlog / maintenance** — flaky tests, dependency bumps, that one deprecated endpoint three external teams still hit.

Three workers are running from last week. One finished the notification service's gRPC endpoint definitions over the weekend — there's a PR up. Another is mid-way through migrating the delivery-pipeline repo's logging. The third is idle.

---

## First hour: reviews and decisions

You start with the PR from the notifications work. The gRPC proto definitions look right — field numbering follows your conventions, the streaming endpoint for real-time delivery status is there. But the worker used a unary call for batch sends. You leave a comment: "This should be a client-streaming RPC. Batch sizes vary from 10 to 50k during campaigns. Unary with a repeated field will hit the 4MB message limit." You assign the worker to address it.

Next, the logging migration worker put up a PR for the delivery-pipeline repo. This one's interesting — it had a custom log appender that wrote to a shared NFS volume for compliance. You check the PR: the worker replaced it with a structured JSON logger writing to stdout, which is correct for the new stack, but missed that the compliance team reads from that NFS path. You message the worker: "Keep the NFS appender as a secondary sink for now. File a follow-up ticket to move compliance to the centralized log store. Don't break their pipeline."

These two reviews took 20 minutes. If you'd written the code yourself, each would've been half a day.

---

## Monday afternoon: a production issue

Slack blows up. The notification preferences API is returning 500s. Users can't update their email settings. It's a P1.

You check the logs. It's a database connection pool exhaustion — someone's long-running analytics query is hogging connections on the shared database. This is exactly why you're decomposing the notifications service: it's sharing a database with three other modules.

You spin up a new worker and give it a task: "Investigate connection pool exhaustion on the shared-db notifications schema. The immediate fix is probably increasing pool size or killing the long query. The real fix is a read replica for analytics. Do the immediate fix first, open a PR, then write up a one-pager on the read replica approach."

You go back to triaging with the oncall. The worker finds the blocking query (an unindexed JOIN from the analytics dashboard), kills it, adds a statement timeout, and puts up a PR. You review it in five minutes and merge. P1 resolved.

The worker also drafted a one-pager on the read replica setup. You skim it, add a section about failover, and share it in the notifications project channel. That's next week's problem, but now it's documented.

---

## Tuesday: driving the migration

The structured logging migration is the kind of work that's important but nobody wants to do. Eight repos, mostly mechanical, but each one has its quirks. You've been putting it off because spending a full day per repo on find-and-replace feels like a waste of your time. But security has a deadline.

You create tasks for the remaining five repos. For each one, the task description includes:
- Which logging framework to replace (log4j, commons-logging, or the homegrown wrapper)
- Any known custom appenders or sinks to watch out for
- Link to the migration guide you wrote
- "Run the full test suite. If any test depends on log format parsing, update it."

You assign workers to three of them in parallel. The other two depend on a shared logging config library that needs updating first, so you mark those as blocked.

By end of day, two PRs are up. One is clean — straightforward log4j to structured logging swap. The other worker flagged something: "Found a custom metrics appender that extracts latency percentiles from log lines. This will break if we change the log format. Should I migrate this to proper metrics instrumentation, or preserve the log format for this service?"

Good question. You message back: "Migrate to proper metrics. Use the micrometer library — there's already a dependency on it in this repo. Emit p50/p95/p99 histograms instead of parsing logs. This is better anyway." That's the kind of decision only you can make, and it took 30 seconds.

---

## Wednesday: the decomposition continues

The notifications service extraction is the big one. You've broken it into phases:

**Phase 1** (done): Define the gRPC API contract and proto definitions.
**Phase 2** (in progress): Build the new service — data layer, business logic, delivery pipeline integration.
**Phase 3** (next): Dual-write migration to move consumers from the monolith library to the new service.
**Phase 4** (later): Decommission the old code path and drop the shared database tables.

Phase 2 has six tasks. Two are done (schema setup and the preferences CRUD). The gRPC endpoint worker addressed your streaming feedback and the PR is updated. Three more tasks are in the backlog.

You assign a worker to "Implement the delivery pipeline integration — the new service needs to call the email provider and push notification gateway. Use the same retry/backoff logic from the monolith but extract it into a reusable package." You link the relevant monolith code file and the provider API docs.

Another worker picks up "Write the data migration script — dual-write from the monolith to the new service's database, with a consistency checker that compares records nightly." You add a note: "Use the existing CDC framework. Don't build a custom sync. The consistency checker should alert but not block writes."

You spend the rest of the morning in a design review for the Phase 3 consumer migration plan. The workers keep going while you're in meetings.

---

## Thursday: tech debt and cleanup

You finally have a window. That flaky test suite in the notifications module has been failing intermittently for three months. The team just re-runs and hopes. You know it's a test isolation issue — tests share a database and don't clean up.

You create a task: "Fix test isolation in the notifications module test suite. Each test should get its own database transaction that rolls back after the test. Look at how the users-service tests handle this — they have a good pattern with test fixtures. Target: zero flaky test failures in 50 consecutive runs."

You assign a worker. It comes back an hour later with a PR that refactors 40 tests to use transaction-scoped fixtures. The notes say: "Found 3 tests that were also sharing an in-memory cache instance. Fixed those too. Ran the suite 50 times, all green."

While that's happening, you notice a notification from yesterday's logging migration work. One of the PRs got review comments from the service owner about a config file path change. You forward the comment to the worker: "Address the review — they're right about the config path. Use the $SERVICE_HOME environment variable, not a hardcoded path."

---

## Friday: stepping back

You open the dashboard for the weekly check-in.

**Notifications Service Extraction**
- gRPC contract done, PR merged
- Preferences CRUD done
- Delivery pipeline integration in progress
- Data migration script has a PR up
- Phase 2 is 60% through

**Structured Logging Migration**
- 5 of 8 repos done
- 2 remaining repos unblocked now that the shared config library is updated
- 1 repo needed a detour into proper metrics instrumentation (better outcome than the original plan)

**Maintenance**
- P1 connection pool issue fixed, read replica one-pager shared
- Flaky test suite fixed, 50 consecutive green runs
- 2 PRs with review feedback addressed and merged

You shipped across 6 repos this week. You reviewed PRs, made architectural decisions, handled a production incident, fixed long-standing tech debt, and drove a cross-team migration forward. You attended your design reviews and planning meetings. You were on Slack when people needed you.

The workers wrote the code. You provided the judgment — which approach to take, where the edge cases are, what "done" means for each task. You caught the things an AI wouldn't know: that compliance reads from the NFS path, that the batch endpoint will hit the message size limit during campaigns, that the flaky tests are a cache isolation issue.

That's the workflow. You think, decide, and review. The workers execute. The dashboard shows you where everything stands. The notifications tell you when something needs your attention. You stay in control of everything without being the bottleneck on everything.
