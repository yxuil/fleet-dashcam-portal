import { Placeholder } from "./Placeholder";

export function CasesPage() {
  return (
    <Placeholder
      title="Cases"
      hint="Case list and management. Implemented in T14."
    />
  );
}

export function CaseDetailPage() {
  return (
    <Placeholder
      title="Case"
      paramNames={["id"]}
      hint="Case detail view. Implemented in T14."
    />
  );
}
