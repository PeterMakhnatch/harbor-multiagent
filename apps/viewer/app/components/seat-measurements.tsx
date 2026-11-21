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
        <CardDescription>Setup and execution show recorded wall intervals that can overlap.</CardDescription>
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
