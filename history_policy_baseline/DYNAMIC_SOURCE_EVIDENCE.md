# Baseline-rooted preservation, 9 September 2026

The original baseline and identity inventory are unchanged. The new pinned
`dynamic_source_evidence_2026-09-09.json` authenticates every eligible original:
1,050 baseline **open, legacy-format** records, covering 78 original key fixture
groups, dated 8–20 September. It does not approve new source identities, settled
renames, arbitrary Sofa IDs, arbitrary aliases, or lower history counters.

The committed outer SHA256 is
`ededd40c007d447db7fe48e6b9c0624dfdd3b627f131a0b57d48624437949148`.
The decoded 2,867,593-byte index has SHA256
`5c923a804028693cf50172cf76585c0a23ffd6e84628b6a6716727b7dde1c72a`.
There are 542 originals with an authenticated Sofa anchor and 508 legacy-only
originals. An independent read-only replay of the current 412 literal missing
identities validated every actual successor against this index (412/412).

The source builder reads every original shard from exact source commit
`cef4b986941a2a6e9bedec1eb1947d4698fcf647` and verifies its SHA256 against the
unchanged baseline metadata. It retains complete original records and their
digests. For the existing 331 independently audited remaps, the previous audited
record remains an additional observation/price floor, including the specifically
proven earlier opening timestamps. The old certificate is unchanged.

Each original receives an immutable fixture contract: exact kickoff instant,
ordered approved team-name pairs, permitted legacy civil dates, explicit allowed
Sofa event IDs, and scoped source league labels. Fixture anchors come from exact
historical Git fixture snapshots, the existing closed certificate, and explicitly
reviewed event-scoped fixture/name anchors. Original names, competition context,
event/team IDs and full source receipts remain inspectable in the decoded index.
No fuzzy score or unverified `sofa_id` self-claim can authorize a future rename.
Source fixtures with no reviewed Sofa anchor remain **legacy-only**; their future
promotion to an unknown Sofa ID deliberately requires fresh independent evidence.

At build time, each missing eligible original must have exactly one active owner
whose direct flattened `merged_from_keys` ancestry includes that original. The
validator compares that actual record directly with the immutable source and
audited floors: house, market, line, side, kickoff, ordered raw and normalized
team names, competition label, opening odd/time, extrema, counters and last
observation time. It additionally checks the target **key**: a legacy key must
contain approved fixture names/date; a Sofa key must contain an explicitly
anchored event ID matching the record. Copies are sealed before view mutations.

Consequently a second rename can pass without modifying the source index if the
final target remains inside the same authenticated fixture contract and retains
direct original ancestry. There is no lineage-DAG traversal, inferred ancestor,
retired/tombstone record loader, or alias insertion into the active inventory.
Two missing originals cannot reuse one witness target. A target may itself be a
literal baseline identity, as already allowed by 10 original closed-certificate
merges; this never adds a synthetic record to raw counts. Active/settled counts
remain real and every existing count floor remains enforced.

## Offline runtime and reproduction

Production runtime verifies the outer file's pinned SHA256 in
`history_policy_dynamic.py`, then the packed JSON envelope's decoded SHA256 and
byte count. The gzip/base64 payload avoids duplicating multi-megabyte fixture
receipts on disk. The decoded object is the complete inspectable source index.
Plain JSON is also supported for isolated fixtures/tests. Runtime does not read
Git, call a network service, or depend on the rolling current fixture window, so
shallow CI checkouts and fixtures aging out do not remove existing proof.

Offline reproduction from the repository root requires the named Git objects:

```sh
python history_policy_baseline/build_dynamic_source_evidence.py \
  --fixture-commit 4f7ab3cc8d31854110d0243f12188622a8fe2391 \
  --fixture-commit e494cfd69fe968da54a2c62899ccdf186b93399d \
  --fixture-commit d2b316e17a7f358f514dd90ac29a8b1c79981728 \
  --fixture-commit be7ca47926d321c3a10076fdb2e6687b3b6f856d \
  --fixture-aliases history_policy_baseline/fixture_alias_anchors_2026-09-09.json \
  --packed
```

Omit `--packed` to inspect full JSON or use `--report-only` for per-fixture source
coverage and Sofa-anchor availability. The builder prints generated evidence;
it never updates raw history, original baseline files, or the runtime pin.
Optionally use `--output /absolute/new/output.json` to save the generated artifact
directly. An existing path is only accepted when its contents are identical;
different evidence is never silently overwritten.

Strict limits intentionally remain: changed opening price, an unapproved earlier
opening observation, ambiguous ancestry, source/fixture tampering, different
home/away orientation or category, unknown Sofa IDs and lost settled IDs fail
closed. Legitimate changes outside this finite authenticated contract need
independent evidence rather than an automatic approval-list expansion.
