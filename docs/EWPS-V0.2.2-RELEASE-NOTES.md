# EWPS v0.2.2 Alpha — Start-to-start Cadence Instrumentation

**Experimental pre-release:** `ewps-v0.2.2-alpha`

EWPS v0.2.2 is a narrowly scoped instrumentation correction on the
`research/ewps-v0.2` line. The mathematical model remains `0.2.0`.

## Corrected interval semantics

`sampleIntervalSeconds` now means measurement-cycle start-to-start cadence.
The collector remains single-threaded at the cycle level, so probe cycles
cannot overlap. A five-second interval with four seconds of collection waits
approximately one additional second before the next cycle. If collection
exceeds the interval, the next cycle starts as soon as the current cycle is
safe and the cumulative cadence-overrun count increments.

Each new live decision records the configured interval, cycle start and
completion timestamps, collection duration, actual start-to-start interval
(from the second cycle onward), and cumulative cadence-overrun count. These
fields are additive and are shown in the live Observatory and completed
experiment summary. JSONL/JSON/CSV exports include the same instrumentation.

Historical v0.1 and v0.2 decisions omit the optional cadence envelope and
continue to parse and replay through their recorded model version. Cadence is
not an EWPS engine input and is excluded from the deterministic replay digest,
preserving historical v0.2 digests.

## Workload label

The new-experiment workload list includes `Background streaming` as a generic
operator label. Existing experiment and measurement labels are never rewritten.

## Explicit non-changes

This release does not change confidence weights, raw-performance weights,
impairment profiles, hysteresis, loss weighting, topology semantics,
controlled-path identity or architecture, routing, or traffic steering.
