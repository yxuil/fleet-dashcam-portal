/**
 * Generic placeholder used while feature tasks (T11–T14) are still TBD.
 *
 * One-file-per-component is overkill for stubs, so the route entry
 * points re-export tiny wrappers around this.
 */

import { useParams } from "react-router-dom";

type Props = {
  title: string;
  /** Comma-separated route param names to surface for debugging. */
  paramNames?: readonly string[];
  /** Short description displayed under the title. */
  hint?: string;
};

export function Placeholder({ title, paramNames, hint }: Props) {
  const params = useParams();
  return (
    <section className="space-y-3">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {hint ? <p className="text-sm text-muted-foreground">{hint}</p> : null}
      {paramNames?.length ? (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
          {paramNames.map((name) => (
            <div key={name} className="contents">
              <dt className="font-mono text-muted-foreground">{name}</dt>
              <dd className="font-mono">{params[name] ?? "—"}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </section>
  );
}
