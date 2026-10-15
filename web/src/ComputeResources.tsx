import React from "react";
import { ComputeResources as Limits, RESOURCE_FIELDS } from "./compute-api";

export function ComputeResources({ value, maximum, disabled, onChange }: {
  value: Limits; maximum?: Limits; disabled?: boolean; onChange: (value: Limits) => void;
}) {
  return <fieldset disabled={disabled} className="org-compute-resources"><legend>Resource limits</legend>
    <p className="org-help">1 MiB = 1048576 bytes; 1 GiB = 1073741824 bytes. Limits apply to one job, not the cloud invoice.</p>
    <div>{RESOURCE_FIELDS.map(([key, label, low, upper]) => <label key={key}>{label}
      <input required type="number" min={low} max={Math.min(upper, maximum?.[key] ?? upper)} step={1} value={Number.isNaN(value[key]) ? "" : value[key]}
        onChange={(event) => onChange({ ...value, [key]: event.target.value === "" ? NaN : Number(event.target.value) })} />
    </label>)}</div>
  </fieldset>;
}
