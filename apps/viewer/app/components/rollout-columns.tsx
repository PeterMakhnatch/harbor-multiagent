import type { ColumnDef } from "@tanstack/react-table";
import { RewardCell } from "~/components/reward-cell";
import { SortableHeader } from "~/components/ui/data-table";
import {
  elapsedTimestampMs,
  parseAbsoluteTimestampMs,
  UNKNOWN_TIME_LABEL,
} from "~/lib/timestamps";
import type { TrialSummary } from "~/lib/types";
import { formatCostUSD, formatStatus } from "~/lib/utils";

function formatTokens(n: number | null): string {
  if (n === null) return "-";
  return Math.round(n).toLocaleString();
}

function formatDurationMs(durationMs: number): string {
  const seconds = Math.floor(durationMs / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);

  if (hours > 0) {
    return `${hours}h ${minutes % 60}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds % 60}s`;
  }
  return `${seconds}s`;
}

export const rolloutStatusColumn: ColumnDef<TrialSummary> = {
  accessorKey: "state",
  header: "Status",
  cell: ({ row }) => (
    <span className="whitespace-nowrap">{formatStatus(row.original.state)}</span>
  ),
};

export const rolloutColumns: ColumnDef<TrialSummary>[] = [
  {
    accessorKey: "name",
    header: ({ column }) => (
      <SortableHeader column={column}>Rollout</SortableHeader>
    ),
  },
  {
    accessorKey: "task_name",
    header: ({ column }) => (
      <SortableHeader column={column}>Task</SortableHeader>
    ),
  },
  {
    accessorKey: "agent_name",
    header: ({ column }) => (
      <SortableHeader column={column}>Agent</SortableHeader>
    ),
    cell: ({ row }) => row.original.agent_name || "-",
  },
  {
    accessorKey: "model_provider",
    header: ({ column }) => (
      <SortableHeader column={column}>Provider</SortableHeader>
    ),
    cell: ({ row }) => row.original.model_provider || "-",
  },
  {
    accessorKey: "model_name",
    header: ({ column }) => (
      <SortableHeader column={column}>Model</SortableHeader>
    ),
    cell: ({ row }) => row.original.model_name || "-",
  },
  {
    accessorKey: "source",
    header: ({ column }) => (
      <SortableHeader column={column}>Dataset</SortableHeader>
    ),
    cell: ({ row }) => row.original.source || "-",
  },
  {
    accessorKey: "reward",
    header: ({ column }) => (
      <div className="text-right">
        <SortableHeader column={column}>Reward</SortableHeader>
      </div>
    ),
    cell: ({ row }) => {
      const reward = row.original.reward;
      const errorType = row.original.error_type;

      if (errorType) {
        return <div className="text-right text-destructive">{errorType}</div>;
      }
      if (
        reward === null &&
        Object.keys(row.original.evals ?? {}).length === 0
      ) {
        return <div className="text-right text-muted-foreground">-</div>;
      }
      return <RewardCell primary={reward} evals={row.original.evals ?? {}} />;
    },
  },
  {
    id: "duration",
    accessorFn: (row) => elapsedTimestampMs(row.started_at, row.finished_at),
    header: ({ column }) => (
      <div className="text-right">
        <SortableHeader column={column}>Duration</SortableHeader>
      </div>
    ),
    cell: ({ row, getValue }) => {
      const { started_at, finished_at } = row.original;
      const durationMs = finished_at === null
        ? elapsedTimestampMs(started_at, Date.now())
        : getValue<number | null>();
      if (durationMs === null) {
        return <div className="text-right text-muted-foreground">-</div>;
      }
      const duration = formatDurationMs(durationMs);
      if (finished_at === null) {
        return (
          <div className="text-right text-muted-foreground">{duration}...</div>
        );
      }
      return <div className="text-right">{duration}</div>;
    },
  },
  {
    id: "started_at",
    accessorFn: (row) => parseAbsoluteTimestampMs(row.started_at),
    header: ({ column }) => (
      <SortableHeader column={column}>Started</SortableHeader>
    ),
    cell: ({ row, getValue }) => {
      const ms = getValue<number | null>();
      if (ms === null) {
        return (
          <span className="text-muted-foreground">
            {row.original.started_at === null ? "-" : UNKNOWN_TIME_LABEL}
          </span>
        );
      }
      return new Date(Math.floor(ms)).toLocaleString();
    },
  },
  {
    accessorKey: "input_tokens",
    header: ({ column }) => (
      <div className="text-right">
        <SortableHeader column={column}>Uncached Input Tokens</SortableHeader>
      </div>
    ),
    cell: ({ row }) => {
      const value = row.original.input_tokens;
      if (value === null) {
        return <div className="text-right text-muted-foreground">-</div>;
      }
      return (
        <div className="text-right tabular-nums">{formatTokens(value)}</div>
      );
    },
  },
  {
    accessorKey: "output_tokens",
    header: ({ column }) => (
      <div className="text-right">
        <SortableHeader column={column}>Output Tokens</SortableHeader>
      </div>
    ),
    cell: ({ row }) => {
      const value = row.original.output_tokens;
      if (value === null) {
        return <div className="text-right text-muted-foreground">-</div>;
      }
      return (
        <div className="text-right tabular-nums">{formatTokens(value)}</div>
      );
    },
  },
  {
    accessorKey: "cached_input_tokens",
    header: ({ column }) => (
      <div className="text-right">
        <SortableHeader column={column}>Cached Input Tokens</SortableHeader>
      </div>
    ),
    cell: ({ row }) => {
      const value = row.original.cached_input_tokens;
      if (value === null) {
        return <div className="text-right text-muted-foreground">-</div>;
      }
      return (
        <div className="text-right tabular-nums">{formatTokens(value)}</div>
      );
    },
  },
  {
    accessorKey: "cost_usd",
    header: ({ column }) => (
      <div className="text-right">
        <SortableHeader column={column}>Cost USD</SortableHeader>
      </div>
    ),
    cell: ({ row }) => {
      const cost = row.original.cost_usd;
      if (cost === null) {
        return <div className="text-right text-muted-foreground">-</div>;
      }
      return (
        <div className="text-right tabular-nums">{formatCostUSD(cost)}</div>
      );
    },
  },
];
