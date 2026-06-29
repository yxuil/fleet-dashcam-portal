import { Placeholder } from "./Placeholder";

export function TrucksPage() {
  return (
    <Placeholder
      title="Trucks"
      hint="Truck list and event timeline. Implemented in T13."
    />
  );
}

export function TruckEventsPage() {
  return (
    <Placeholder
      title="Truck events"
      paramNames={["id"]}
      hint="Event timeline for a single truck. Implemented in T13."
    />
  );
}
