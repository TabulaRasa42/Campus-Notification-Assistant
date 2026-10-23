# Issue tracker: GitHub

Issues and specs live in GitHub Issues. Use the `gh` CLI.

Repository: [TabulaRasa42/Campus-Notification-Assistant](https://github.com/TabulaRasa42/Campus-Notification-Assistant).

Infer the repository from `git remote -v` when a remote is configured.
Until then, pass `--repo TabulaRasa42/Campus-Notification-Assistant`
to repository commands.

## Conventions

- Create: `gh issue create --title "..." --body-file <file>`
- Read: `gh issue view <number> --json number,title,body,labels,comments`
- List: `gh issue list --state open --json number,title,body,labels`
- Comment: `gh issue comment <number> --body-file <file>`
- Add labels: `gh issue edit <number> --add-label "..."`
- Remove labels: `gh issue edit <number> --remove-label "..."`
- Close: `gh issue close <number> --comment "..."`

Use UTF-8 files with actual newlines for multiline bodies.
Use the role mappings in `triage-labels.md`.

## Pull requests as a triage surface

**PRs as a request surface: no.**

## Skill operations

“Publish to the issue tracker” means create a GitHub issue.
“Fetch the relevant ticket” means read the referenced GitHub issue.

## Wayfinding operations

- Map: one issue labelled `wayfinder:map`, containing Notes,
  Decisions-so-far, and Fog.
- Child tickets: link them as native sub-issues where available.
  Otherwise, add a task list to the map and `Part of #<map>` to each child.
- Type labels: `wayfinder:research`, `wayfinder:prototype`,
  `wayfinder:grilling`, or `wayfinder:task`.
- Blocking: use native issue dependencies where available.
  Otherwise, record `Blocked by: #<number>` in the description.
- Frontier: open children with no open blockers and no assignee,
  considered in map order.
- Claim: assign the ticket to the driving developer before working.
- Resolve: comment with the answer, close the ticket, and add a
  gist plus link to the map's Decisions-so-far.
