# Issues #3, #5, #7 integration contract

Review fixed point: `926be23` (merged Issue #2, PR #11). Keep Python + PySide6 Qt Quick.
Issue #1 already confirms the test seam: public application service operations, real
temporary SQLite and controlled HTTP responses; Qt/QML interactions at the UI seam.
Tests use an in-memory external keyring backend and offscreen Qt, never real user data.

## Shared behavior and ownership

- #3 owns `models.py`, evidence/time normalization, correction operations, the extraction
  prompt, field display/highlight UI. Existing `Draft.item`, `Todo.item`, service
  `draft/source`, `confirm(id)`, `list_todos/get_todo` stay usable. New model fields need
  defaults so Issue #2 JSON rows load unchanged. Empty extraction returns `None`, never
  a blank Todo. Single current draft stays the scope; multi-item authoring is Issue #4.
- #5 owns HTTP error classification/retry, service request lifecycle and asynchronous
  Controller workers. `extract(*, api_key=None) -> Draft | None` remains the entry point.
  Each request captures immutable source plus a generation; input change, discard or
  a newer extraction invalidates old publication. Stale completion cannot change draft,
  source, current loading/error state or saved data. Prefer a lock around state only,
  never hold it over HTTP. Clear/invalidate draft at the start of a new attempt so failed
  extraction cannot accidentally enable saving an older result. Confirm must serialize
  against edits and save atomically/idempotently.
- #7 owns exact-source duplicate lookup and duplicate decision UI. Add
  `find_duplicates(source: str | None = None) -> list[Todo]`, comparing unmodified text
  and returning every matching row. Duplicate checks precede AI calls in Controller;
  viewing/cancelling never calls AI. Explicit re-extraction follows normal extract and
  confirm, creates another ID and does not update earlier rows. Keep legacy saved rows.
- `ServiceError` remains the safe user-facing exception; #5 may add optional `code`
  and a dedicated stale-request subtype. Never expose transport bodies, secrets or
  notification text in errors. #3 invalid evidence is a review state, not a failed HTTP
  request and never triggers automatic retry.
- #3 may extend Controller result/correction properties and QML field controls;
  #5 edits worker/start/finish/input methods; #7 adds duplicate properties/slots and
  dialog. Make local hunks; report cross-owner interface changes before implementing.
  Primary agent resolves overlap and combined behavior. Final integration order is
  #5, #3, #7: request reservation/locking first, review normalization next, duplicate
  decision UI last. Both extraction entry points reserve their snapshot before queuing.

## Required combined verification

Exact duplicate -> view/cancel without AI; explicit re-extract -> normal confirmation
without overwriting prior rows. Retry temporary errors once only; terminal response
and review states never retry. Older source/request results cannot restore a draft.
Unmatched citations block save until corrected/supplemented/removed; missing and
uncertain times remain saveable and survive restart. Confirm is atomic and repeated
submission is idempotent, including database failure/recovery and legacy data reads.

## Baseline evidence

Original glossary and accepted technical ADR copied byte-for-byte; originals preserved.
Initial static checks passed (ruff, strict mypy). Initial pytest: 23 passed, 1 failed,
4 errors, all five caused by inaccessible real Windows credential storage in tests.
The shared test fixture substitutes only that external boundary; production stays on
Windows credential storage. Baseline is rerun after that fixture is applied.

After external credential isolation: 28 tests passed; ruff passed. Strict mypy passed on baseline src.
