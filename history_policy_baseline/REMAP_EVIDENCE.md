# Closed preservation evidence, 8 September 2026

The original `baseline.json` and `identities.json.gz` are unchanged. Their source
is `cef4b986941a2a6e9bedec1eb1947d4698fcf647`: 835,382 actual raw identities and
346,572 settled identities. This supplement is **not** a replacement baseline,
approval flag, data migration, settlement change, or count adjustment.

`remap_evidence_2026-09-08.json` contains a closed list of 331 exact old/target
pairs, pinned by SHA256 in `history_policy_remap.py`. Each entry includes its
complete original record, original-record digest and original shard SHA256
from the frozen baseline, plus the complete audited successor and its digest.
Per-entry `audited_commit` and per-commit `target_files` receipts identify the
exact successor snapshot; no single snapshot is falsely claimed for all entries.

- 317 pairs were independently re-read from original Git source shards and
  audited target `4f7ab3cc8d31854110d0243f12188622a8fe2391`.
- Two Harborough Town / Hereford pairs were independently verified against
  `e494cfd69fe968da54a2c62899ccdf186b93399d`, after a newer replay correctly
  rejected them as outside the initial closed list. Their candidates are in
  `supplement_candidates_2026-09-08.json`; that file is not an approval. Both
  original keys reappeared literally by the next snapshot, needing no witness.
- Ten Bayer Leverkusen / RB Leipzig pairs and two TPS / SJK pairs were
  independently verified against `d2b316e17a7f358f514dd90ac29a8b1c79981728`.
  These candidates are in `supplement_candidates_2026-09-08_1225.json`.
- All 331 original records were open, never settled. There are 331 unique
  successors: 321 new identities and 10 already in the baseline. The latter are explicitly
  identified by each entry's `target_was_baseline_identity` flag; they do not
  count as newly created or extra raw records.

The builder verifies the entire current raw identity/lineage inventory before
issuing a witness. A missing old key must point to exactly one current owner,
at the exact pinned target. The current record must preserve house, market,
line, side, kickoff, approved Sofa identity and opening odd; retain the audited
opening timestamp (which may be genuinely earlier than the original); and
retain or extend both original/audited extrema, observation counters and latest
observation time. It copies witnesses before further in-memory view deduplication
so later mutations cannot alter the sealed report. No source records are changed.

Schema 2 reports actual raw/settled counts and identity digests, the complete
literal missing-key list, verified remap count, remaining missing count, pinned
certificate hash, and per-record witnesses. The deploy validator rechecks these
witnesses against the pinned certificate. As with schema 1, the final report
is bound to the history artifact and manifest. All original raw/settled and
public `monitoradas`/`liquidadas` count floors remain in force. Aliases are never
inserted into the count inventory, and settled identities cannot use this list.
Schema 1 remains literal-only and retains its existing validation.

Reintroduced literal keys need no remap witness. Missing keys outside the list,
an absent target, ambiguous ancestry, a further rename to another target, a
changed required value, a lost settled key or a count decline all fail closed.
Future successor/ancestry changes need fresh independent evidence and review;
`merged_from_keys` by itself is never accepted as generic preservation.

Read-only replay of the complete Git inventory at
`d2b316e17a7f358f514dd90ac29a8b1c79981728` passed: 844,327 actual raw records,
346,572 settled records, 329 literal missing keys, 329 validated remaps and
zero remaining raw/settled losses. The other two certified old keys were
present literally and were not counted as remaps. The replay retained exact
full candidate records plus every current identity, status and lineage for
the same pure preservation validation; it did not build or publish artifacts.

## Reproduction

From the repository root, with the prior read-only audit available locally:

```sh
python history_policy_baseline/build_remap_evidence.py \
  --audit /absolute/path/to/verificacao_pos_windows_2026-09-08/mesa/lineage_proof.json \
  --snapshot 4f7ab3cc8d31854110d0243f12188622a8fe2391 \
  --supplement-audit history_policy_baseline/supplement_candidates_2026-09-08.json \
  --supplement-audit history_policy_baseline/supplement_candidates_2026-09-08_1225.json
```

The command only reads Git and prints the reproducible certificate. It treats
the prior audit as candidate pairs, not as source-record evidence; original
shards must pass the original baseline hashes. It does not update the pinned
hash, change the baseline, rewrite raw history, or run automatically in CI.
