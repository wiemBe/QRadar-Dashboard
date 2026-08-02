// Every recorded lifecycle transition, as a vertical timeline.
//
// Nothing is filtered. "This incident flapped CANDIDATE/NORMAL six times
// before opening" is a fact about detector tuning, and a view showing only the
// transitions leading to the current state hides exactly the evidence that the
// thresholds need adjusting. Holds caused by incomplete data are shown too.
//
// The nine-column table this replaces spent four of those columns on the same
// value repeated down every row — actor and policy version — so those move
// into a per-transition disclosure where they are still one interaction away.

import { Disclosure } from "@/components/ui/Disclosure";
import type { AnomalyTransition } from "@/lib/api";
import { formatMetric, stateTone } from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

export function LifecycleTimeline({
  transitions,
  policyVersion,
}: {
  transitions: AnomalyTransition[];
  policyVersion: number | null;
}) {
  if (transitions.length === 0) {
    return (
      <div className="notice">
        No transitions recorded. The anomaly exists but its lifecycle audit
        trail is empty, which is itself unexpected — the engine writes a
        transition for every state change.
      </div>
    );
  }

  return (
    <ol className="lifecycle">
      {transitions.map((t, i) => (
        <li key={`${t.occurred_at}-${t.to_state}-${i}`} className="lifecycle-item">
          <div className="lifecycle-marker" aria-hidden="true" />
          <div className="lifecycle-body">
            <div className="row lifecycle-states">
              {/* The first transition has no previous state. That is an
                  origin, not an unknown, and is labelled as such. */}
              {t.from_state ? (
                <span className={`pill pill-quiet ${stateTone(t.from_state)}`}>
                  {t.from_state}
                </span>
              ) : (
                <span className="muted">initial</span>
              )}
              <span className="muted" aria-hidden="true">
                →
              </span>
              <span className={`pill ${stateTone(t.to_state)}`}>{t.to_state}</span>
              <time className="muted num" dateTime={t.occurred_at}>
                {formatDateTime(t.occurred_at)}
              </time>
            </div>

            <p className="lifecycle-reason">
              {t.reason ?? <span className="muted">No reason recorded</span>}
            </p>

            <Disclosure summary="Transition detail">
              <dl className="dimension-meta">
                <div>
                  <dt>Bucket</dt>
                  <dd className="num">{formatDateTime(t.bucket_start)}</dd>
                </div>
                <div>
                  <dt>Observed</dt>
                  <dd className="num">{formatMetric(t.observed_value)}</dd>
                </div>
                <div>
                  <dt>Expected</dt>
                  <dd className="num">{formatMetric(t.expected_value)}</dd>
                </div>
                <div>
                  <dt>Actor</dt>
                  <dd>{t.actor}</dd>
                </div>
                <div>
                  <dt>Policy version</dt>
                  <dd className="num">
                    {policyVersion != null ? `v${policyVersion}` : "—"}
                  </dd>
                </div>
              </dl>
            </Disclosure>
          </div>
        </li>
      ))}
    </ol>
  );
}
