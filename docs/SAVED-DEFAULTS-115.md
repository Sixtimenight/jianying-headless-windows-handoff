# Saved fps and speed defaults on Jianying 11.5

Jianying 11.5 can omit `fps: 30`, speed-material `speed: 1`, and video/audio
segment `speed: 1` when it saves a draft. `edit verify` previously reported
these omissions as lost nonempty fields. In the reverse direction, a saved
input that already omitted a default could hide a newly added nondefault value.

`native_edit.compare_saved_timeline(expected, actual, runtime_profile)` now
interprets these defaults on independent comparison copies, then uses the
existing preservation comparator. `verify_live()` passes the profile returned
by `doctor()` after handling the existing reviewed schema migration. Neither
the expected snapshot nor the saved draft is rewritten.

## Exact scope

Default interpretation requires `jy14-headless-macos-11.5.0`. Both input graphs
must pass the existing compound/basic structural validation and the runtime
schema validator. Recognized locations are:

| Owner | Missing field | Meaning |
| --- | --- | --- |
| Root or real child timeline from `native_compound.graph()` | `fps` | `30` |
| Direct `materials.speeds` node whose `type` is `speed` | `speed` | `1` |
| Direct video-track segment referencing the `videos` bucket | `speed` | `1` |
| Direct audio-track segment referencing the `audios` bucket | `speed` | `1` |

Speed defaults are filled only for ordinary video/audio (integer mode 0 or
absent, null/absent/empty-string curve) and native custom video curves (integer
mode 1, custom identity `6768730851543880206`, at least two finite positive
speed points with strictly increasing x from 0 to 1). The segment must reference
the matching media bucket and exactly one speed material. Every owner of a
speed material must meet these conditions. Orphan materials do not qualify.
Ordinary audio now has actual UI save/cold-reopen evidence, described below.
Missing speed in other recognized video/audio contexts is rejected on either
side, even if both sides omit it. Explicit speeds remain comparable. This
deliberately withdraws default filling for mode-1 null/empty curves and unknown
curve identities; it does not claim those shapes passed UI acceptance.
Text, effect, unknown track kinds and unknown material buckets receive
no segment-speed exception. Similarly named fields below plugins are untouched.
Real nested timelines receive the same interpretation, but this does not enable
compound home registration or establish native compound save persistence.

Only an absent key is filled. Present values must be positive finite numbers;
`null`, booleans, strings and other invalid values are rejected by this 11.5
comparison path. This prevents Python's `True == 1` equality from treating a
boolean as a speed default. Explicit numbers retain the original `1e-5`
absolute comparison tolerance. The existing per-frame source/target time
quantization tolerance and report are unchanged.

Both sides receive the interpretation: omitted speed versus `1.5`, or omitted
fps versus `60`, is a mismatch in either direction. Entire material/segment
loss, unknown nonempty field loss and unreviewed compound structures still
fail. This change is not a general bidirectional rewrite of arbitrary JSON:
the original comparator's policy for newly added unknown fields remains.

For each known speed material, the saved mode and complete curve must also
match the expected context. A newly added mode or curve cannot pass merely
because both sides independently qualify for default filling. Absent mode
and integer mode 0 are equivalent; ordinary-mode absent/null/empty-string
curves are equivalent. Other mode types and curve changes remain distinct.
This check follows real child timelines and does not reinterpret plugin data.

Known 11.4 profiles retain the upstream preservation comparison without new
default filling. This includes its existing one-way exception for omitted unit
speed on uncurved speed materials; fps and segment omissions remain strict.
Unknown profiles and incompatible schemas fail the existing schema validator.
Generic `preserved()` is unchanged; source, frozen build and sidecar callers
continue to use its existing rules.

## Mode/curve evidence and its limits

The 2026-09-21 diagnostic used the exact 11.5 native model's
`GetDraftFromJson` / `GetJsonFromDraft`, with one referenced video speed
material in each of four fixed synthetic inputs:

| Shape | `mode` | `curve_speed` |
| --- | --- | --- |
| Ordinary | 0 | null |
| Nonordinary mode | 1 | null |
| Empty curve | 1 | object with an empty `speed_points` list |
| Three-point curve | 1 | points (0, 1), (0.5, 2), (1, 1) |

Within each shape, input copies differed only in the material's `speed`:
explicit 1, absent, or 1.5. Explicit 1 and absence produced byte-identical
complete native JSON; 1.5 produced different output. The existing comparison
entry accepted the equivalent pair and rejected the nondefault pair. The
55-file historical manifest was rechecked on 2026-09-22, and both directions
were replayed against the final comparator using the original synthetic
inputs and captured native outputs; updated outcomes distinguish these two sources. This replay did not execute the engine.
Hashes, runtime identity and per-shape results are in the
[evidence summary](evidence/speed-defaults-115.json).

The parser experiment does **not** establish UI save/cold-reopen or playback.
The final candidate therefore narrows default filling to the supported owner
contexts above. Replaying historical synthetic inputs now rejects missing speed
in mode-1 null/empty and `synthetic-curve` contexts. Their captured native outputs
already contain explicit speed 1, so identical output pairs remain comparable
without filling any default. The historical parser facts are unchanged.

Regression covers both comparison directions, linked speeds, references, mode,
points and immutable inputs through `verify_live()`. Rejection controls for
unverified shapes remain synthetic. `preserved()` and 11.4 keep existing rules.
Official application, library and codec pins are unchanged.

## Native UI copy, save and cold reopen on 2026-09-22

A generated silent video fixture contains ordinary unit speed, constant 1.5x,
a five-point flat custom curve and a five-point nonflat custom curve. Both
curves were created through Jianying 11.5's UI, not by assigning a presumed
mode or curve shape in JSON. An independent edit copy only renames the track.
It was opened, played, saved, fully quit and cold reopened; playback was again
observed from zero to 11 seconds and 2 frames, and all three speed panels were
checked. The final complete readback verifies source files unchanged, four
equal mirrors and preserved content. This uses the recorded local codec, not
the upstream pinned codec.

The first save exposed a false rejection: the nonflat curve's linked segment
and material average both changed from `1.4488572870241114` to
`1.4516133714881843`. The source duration stays 3,000,000 microseconds and the
target duration stays 2,066,666 microseconds. The saved average equals their
ratio; the previous implied duration differs by about 3,931 microseconds, less
than one frame. The native UI's own Copy command reproduced the same change
without a headless edit. All curve points and source/target ranges remained
unchanged. The original failure, encrypted scene and full raw snapshots remain
available locally; the same unchanged failure scene passes the corrected check.

`normalize_saved_curve_averages()` is used only by `verify_live()`. It works on
a comparison copy and reports `native_curve_speed_recomputations`. Its accepted
context is deliberately limited to the exact 11.5 profile, saved schema 187,
30 fps, noncompound video, and a uniquely referenced custom speed material.
The material must keep integer mode 1 and the observed custom-curve identity,
with ordered finite nonflat points. Segment/material averages must agree on
each side, be present and nonunit; all material fields except the average,
references and source/target ranges must match. Within each existing range,
an omitted start and integer `start: 0` are equivalent, so normal zero-start
serialization can accompany average recomputation. Both starts and durations
must be integers; durations must stay positive and identical. Nonzero start
changes, booleans, floating-point range values and any other field differences
do not qualify. The new average must equal
source/target duration, the target must be frame aligned within one microsecond,
and the old implied duration must exceed it by less than one frame.

This does not fill a missing nondefault speed or normalize flat/empty/preset
curves, audio, shared speed owners, changed timing, changed points or changed
references. Generic `compare_saved_timeline()` and `preserved()` remain strict
for the recomputed average. Regression covers the full verification entry,
zero-start/recomputation combinations and negative boundaries; 30 additional two-way comparisons replay the
actual cold-saved document's independent JSON copies. No live draft was changed
to manufacture a negative case. The [captured evidence](evidence/native-save-115.json)
contains selected original fields, source hashes, GUI event/capture identities,
the native-copy control and both readback results. It is not a substitute for
the locally retained full raw documents or screenshots.

This closes local UI save/cold-reopen acceptance for these four video contexts.
It does not enable curve export or remove the remaining
[fixed-codec validation gap](COMPATIBILITY-115-ACCEPTANCE.md).

## Native check of large relative average changes on short curves

A review example used 120,000 microseconds of source over 33,333 microseconds
of timeline, changing both stored averages from 2.0 to approximately 3.600036.
To determine whether accepting that change hides a playback difference, a
diagnostic used the actual saved custom-curve shape at four durations, with an
ordinary tail, on the signed official 11.5 library and recorded local codec.
Only the segment/material averages differ between the paired inputs; the curve,
references and source/target ranges are identical. The native parser preserves
the different averages, so equal results are not caused by parsing them away.

All 189 native time-mapping samples match. Both rendered outputs pass the exact
90-frame requirement and full decoding; all decoded frame hashes match. Changing
the curve's middle point instead changes 99 time-mapping samples and 40 source
frame indices over common output positions. That control produced only 89 of
90 frames and was correctly rejected as a complete export. Its available frames
and independent time queries establish sensitivity only; the failed export is
retained, without retries or a weaker frame requirement.

These results do not support treating the relative average change as a playback
defect in this reviewed context. H1 is removed from the acceptance backlog; the
existing restricted comparison stays unchanged and no percentage limit is added.
The [native evidence summary](evidence/curve-average-native-115.json) records
inputs, runtime identities, comparisons and the rejected control. This is a
derived diagnostic, not new UI persistence acceptance, public curve-export
support or the upstream fixed-codec A/B.

## Ordinary audio and native curve reset on 2026-09-22

A separate six-second fixture contains two audio segments: three seconds at 1x,
then three seconds at 1.5x using 4.5 seconds of source audio. An independent copy
renames only the video/audio tracks. It completed actual save, full quit, cold
reopen, playback to six seconds, speed-panel inspection and another save/quit.
Both full readbacks pass: source unchanged, preserved fields verified and four
mirrors equal. Saved ordinary audio omits unit speed on both segment and material;
the 1.5x segment retains 1.5 on both. This is actual UI persistence evidence using
generated tone media and the recorded local codec; it is not exported-audio or
subjective listening acceptance.

In the same source fixture, selecting None after a custom video curve produces
ordinary mode. Moving a custom point then selecting Reset retains mode 1 and
five flat points. Neither observed path produces mode-1 null/empty curves.
Those shapes are excluded from default filling rather than asserted to work.

The first independent copy was correctly rejected because a saved last-frame
export selection disappeared (`/config/export_range`). Its expected and actual
captures remain intact. A distinct copy was then built from the already-saved
source with no export selection; both subsequent save checks pass. Export-range
comparison was not relaxed. This audio result does not validate preserving an
active export selection across UI playback.

Forty-two two-way offline controls use independent copies of the actual cold-save
document: unit defaults, nondefault losses/changes, mode/reference/curve changes,
native flat-reset defaults, and synthetic null/empty rejection cases. All match
expectations. The previous 30 video controls still pass with this comparator.
See [audio evidence](evidence/audio-save-115.json) for selected fields, reports,
failed-run identity, UI capture hashes and the distinction between real observations
and synthetic mutations. Full raw captures remain local.

## Current-host OS stamp during the reviewed schema upgrade

A fresh native save also showed `last_modified_platform.os_version` changing
from the blueprint's original OS version to the current Mac's version. The
source `platform` remained unchanged. `verify_live()` now recognizes this
last-save stamp only inside the existing reviewed `185.0.0` to `187.0.0`
migration on the exact 11.5 profile. Both last-modified `os` labels must be
`mac`; old, saved and current-host versions must be nonempty dotted numeric
strings, and the saved version must exactly equal `platform.mac_ver()[0]`.

The comparison copy retains the prior stamp so the content comparison can
proceed. The schema-upgrade report includes `last_modified_os_version_before`
and `last_modified_os_version_after`. Neither saved bytes nor frozen evidence
is rewritten. Source `platform`, other platform fields, unchanged schemas,
legacy/unknown profiles, invalid values and noncurrent OS stamps receive no new
exception. Public tests use synthetic OS values and no captured device IDs.

## Offline regression

From the repository root:

```sh
python3 -m unittest discover -s tests -p test_native_defaults_115.py -v
python3 -m unittest discover -s tests -p test_native_curve_average_115.py -v
```

The fixture covers both directions of default omission, explicit nondefault
changes, lost nodes, plugin suffixes, genuine child timelines, invalid types,
numeric/frame tolerances, input immutability, 11.4 controls and invalid profiles.
It also covers current-host OS stamping on the exact schema migration and
rejection outside that context, through the actual `verify_live()` entry.

It also invokes the production `verify_live()` entry with temporary synthetic
files. Only the codec reader, runtime doctor and draft root are substituted;
the actual comparison, evidence hashes, registration, source manifest and four
separate mirror files are checked. This catches missing production wiring while
remaining runnable without an editor, codec, media, network or user drafts.

## Native acceptance

Offline tests do not establish an actual save/cold-reopen result. For that
acceptance, build a fresh normal draft and an independent edit copy using the
reviewed runtime. Open only the copy, play, save, quit, cold-reopen and quit
again; run the full `edit verify --build ...` command afterwards. Retain the
source manifest, saved timeline and verification report. The source must remain
unchanged, the edit must persist, and all four live mirror files must agree.

Negative default-value experiments belong in frozen JSON copies, never in a
user's registered draft. A field-level replay of a historical capture does not
substitute for this fresh native acceptance.
