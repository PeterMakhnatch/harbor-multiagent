import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "~/components/ui/table";
import { elapsedTimestampMs } from "~/lib/timestamps";
import type { ModelInfo, SeatResult, TimingInfo } from "~/lib/types";

const UNKNOWN = "Unknown";
const NUMBER_CELL_CLASS = "text-right tabular-nums";

type ReportedCallCoverage =
  | { kind: "valid"; observed: number; unknown: number }
  | { kind: "unreported" }
  | { kind: "invalid" };

function isCallCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function parseReportedCallCoverage(metadata: unknown): ReportedCallCoverage {
  if (metadata == null) return { kind: "unreported" };
  if (typeof metadata !== "object" || Array.isArray(metadata)) return { kind: "invalid" };

  const hasStarted = Object.hasOwn(metadata, "started_calls");
  const hasObserved = Object.hasOwn(metadata, "observed_usage_calls");
  const hasUnknown = Object.hasOwn(metadata, "unknown_usage_calls");
  if (!hasStarted && !hasObserved && !hasUnknown) return { kind: "unreported" };
  if (!hasStarted || !hasObserved || !hasUnknown) return { kind: "invalid" };

  const {
    started_calls: started,
    observed_usage_calls: observed,
    unknown_usage_calls: unknown,
  } = metadata as Record<string, unknown>;
  if (!isCallCount(started) || !isCallCount(observed) || !isCallCount(unknown)) {
    return { kind: "invalid" };
  }

  const total = observed + unknown;
  if (!Number.isSafeInteger(total) || total !== started) return { kind: "invalid" };
  return { kind: "valid", observed, unknown };
}

export function formatModelInfo(modelInfo: ModelInfo | null | undefined): string | null {
  if (!modelInfo) return null;
  return modelInfo.provider
    ? `${modelInfo.provider}/${modelInfo.name}`
    : modelInfo.name;
}

function formatSeatSeconds(timing: TimingInfo | null | undefined): string {
  const elapsedMs = elapsedTimestampMs(timing?.started_at, timing?.finished_at);
  return elapsedMs === null ? UNKNOWN : (elapsedMs / 1000).toFixed(3);
}

function ReportedCallCoverageCell({ metadata }: { metadata: unknown }) {
  const coverage = parseReportedCallCoverage(metadata);
  if (coverage.kind === "unreported") return <TableCell>Not reported</TableCell>;
  if (coverage.kind === "invalid") return <TableCell>Invalid report</TableCell>;

  const { observed, unknown } = coverage;
  const label = unknown > 0 ? "Partial usage" : observed === 0 ? "No reported calls" : null;
  return (
    <TableCell className="tabular-nums">
      <div>{`${observed.toLocaleString()} observed / ${unknown.toLocaleString()} unknown`}</div>
      {label && <div className="text-muted-foreground">{label}</div>}
    </TableCell>
  );
}

function SeatMeasurementRow({ seat }: { seat: SeatResult }) {
  const { agent_info: agent, agent_result: result, exception_info: error } = seat;
  const cost = result?.cost_usd;
  return (
    <TableRow>
      <TableHead scope="row">{seat.seat}</TableHead>
      <TableCell>{seat.under_test ? "Yes" : "No"}</TableCell>
      <TableCell>{agent?.name ?? UNKNOWN}</TableCell>
      <TableCell>{formatModelInfo(agent?.model_info) ?? UNKNOWN}</TableCell>
      <TableCell className={NUMBER_CELL_CLASS}>{result?.n_input_tokens?.toLocaleString() ?? UNKNOWN}</TableCell>
      <TableCell className={NUMBER_CELL_CLASS}>{result?.n_cache_tokens?.toLocaleString() ?? UNKNOWN}</TableCell>
      <TableCell className={NUMBER_CELL_CLASS}>{result?.n_output_tokens?.toLocaleString() ?? UNKNOWN}</TableCell>
      <TableCell className={NUMBER_CELL_CLASS}>{cost == null ? UNKNOWN : `$${cost}`}</TableCell>
      <ReportedCallCoverageCell metadata={result?.metadata} />
      <TableCell className={NUMBER_CELL_CLASS}>{formatSeatSeconds(seat.agent_setup)}</TableCell>
      <TableCell className={NUMBER_CELL_CLASS}>{formatSeatSeconds(seat.agent_execution)}</TableCell>
      <TableCell title={error?.exception_message}>
        {error === null ? "None recorded" : error?.exception_type ?? UNKNOWN}
      </TableCell>
    </TableRow>
  );
}

export function SeatMeasurements({ seats }: { seats: readonly SeatResult[] }) {
  return (
    <Card className="min-w-0 gap-3 border-b-0 py-4">
      <CardHeader>
        <CardTitle id="seat-measurements-title" className="font-medium">Seat measurements</CardTitle>
        <CardDescription>
          Setup and execution show recorded wall intervals that can overlap. Recorded costs can be
          partial and do not verify a provider bill.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table aria-labelledby="seat-measurements-title">
          <TableHeader>
            <TableRow>
              <TableHead scope="col">Seat</TableHead>
              <TableHead scope="col">Under test</TableHead>
              <TableHead scope="col">Agent</TableHead>
              <TableHead scope="col">Model</TableHead>
              <TableHead scope="col" className="text-right">Input incl. cache</TableHead>
              <TableHead scope="col" className="text-right">Cached input</TableHead>
              <TableHead scope="col" className="text-right">Output</TableHead>
              <TableHead scope="col" className="text-right">Recorded cost USD</TableHead>
              <TableHead scope="col">Agent-reported model calls</TableHead>
              <TableHead scope="col" className="text-right">Setup s</TableHead>
              <TableHead scope="col" className="text-right">Execution s</TableHead>
              <TableHead scope="col">Recorded error</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {seats.map((seat) => <SeatMeasurementRow key={seat.seat} seat={seat} />)}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
