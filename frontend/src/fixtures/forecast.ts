/**
 * Synthetic NovaTech forecast, used until Person 1's engine is behind the tool layer.
 *
 * The shape is the contract; the numbers are illustrative and deliberately tie to the
 * seeded golden path: a $20.0M floor, breached at W6 with $18.4M, driven by a delayed
 * enterprise receipt and a step-up in Dodo declines.
 */

import type {
  CellBasis,
  ForecastCell,
  ForecastRow,
  ForecastSnapshot,
  ForecastWeek,
} from "@/lib/forecast";
import { netForWeek, usd } from "@/lib/forecast";

const WEEK_ENDINGS = [
  "2026-03-06",
  "2026-03-13",
  "2026-03-20",
  "2026-03-27",
  "2026-04-03",
  "2026-04-10",
  "2026-04-17",
  "2026-04-24",
  "2026-05-01",
  "2026-05-08",
  "2026-05-15",
  "2026-05-22",
  "2026-05-29",
];

/** Week 1 has landed; 2-4 are driver estimates; 5-13 rest on assumptions. */
function basisFor(weekIndex: number): CellBasis {
  if (weekIndex === 0) return "actual";
  if (weekIndex <= 3) return "estimate";
  return "assumption";
}

function row(
  category: string,
  direction: ForecastRow["direction"],
  majors: number[],
  options: { reference?: string; staleFrom?: number } = {},
): ForecastRow {
  const cells: ForecastCell[] = majors.map((major, index) => ({
    amount: usd(major),
    basis: basisFor(index),
    reference: options.reference,
    stale: options.staleFrom !== undefined && index >= options.staleFrom,
  }));
  return { category, direction, cells };
}

const FLOOR_MAJOR = 20_000_000;
const CASH_TODAY_MAJOR = 24_800_000;

const ROWS: ForecastRow[] = [
  row(
    "AR collections — enterprise",
    "inflow",
    // Weak from W2 as the Contoso receipt slips; recovers from W7 as the worklist lands.
    [
      4_200_000, 2_600_000, 3_200_000, 2_860_000, 3_790_000, 2_390_000, 5_600_000, 5_190_000,
      5_490_000, 4_120_000, 5_300_000, 5_080_000, 5_670_000,
    ],
    { reference: "ar_ledger:aging-2026-W10" },
  ),
  row(
    "Dodo subscription receipts",
    "inflow",
    [
      1_900_000, 1_750_000, 1_700_000, 1_680_000, 1_650_000, 1_640_000, 1_700_000, 1_720_000,
      1_760_000, 1_780_000, 1_800_000, 1_820_000, 1_840_000,
    ],
    { reference: "dodo:settlement-2026-W10", staleFrom: 4 },
  ),
  row(
    "Other receipts",
    "inflow",
    [
      320_000, 180_000, 210_000, 160_000, 190_000, 150_000, 220_000, 240_000, 200_000, 180_000,
      210_000, 230_000, 220_000,
    ],
  ),
  row(
    "Payroll",
    "outflow",
    [2_400_000, 0, 2_400_000, 0, 2_450_000, 0, 2_450_000, 0, 2_450_000, 0, 2_500_000, 0, 2_500_000],
  ),
  row(
    "AP — vendor payments",
    "outflow",
    [
      3_100_000, 3_400_000, 2_900_000, 3_200_000, 3_300_000, 3_150_000, 2_800_000, 3_050_000,
      2_950_000, 3_100_000, 2_900_000, 3_000_000, 2_850_000,
    ],
    { reference: "ap_ledger:run-2026-W10" },
  ),
  row("Tax and statutory", "outflow", [0, 0, 0, 850_000, 0, 0, 0, 900_000, 0, 0, 0, 940_000, 0]),
  row("Debt service", "outflow", [0, 640_000, 0, 0, 640_000, 0, 0, 640_000, 0, 0, 640_000, 0, 0]),
  row(
    "Operating expenses",
    "outflow",
    [
      1_420_000, 1_390_000, 1_410_000, 1_450_000, 1_440_000, 1_430_000, 1_470_000, 1_460_000,
      1_450_000, 1_480_000, 1_470_000, 1_490_000, 1_480_000,
    ],
  ),
];

/**
 * Closing cash is derived, never typed in: today's balance plus the cumulative net of
 * the rows above. A grid whose total does not tie to its lines is worse than no grid.
 */
const WEEKS: ForecastWeek[] = WEEK_ENDINGS.map((week_ending, index) => {
  const closing =
    usd(CASH_TODAY_MAJOR).minor_units +
    WEEK_ENDINGS.slice(0, index + 1).reduce(
      (total, _, week) => total + netForWeek(ROWS, week).minor_units,
      0,
    );
  return {
    index: index + 1,
    week_ending,
    closing_cash: { minor_units: closing, currency: "USD" },
    basis: basisFor(index),
    breaches_floor: closing < usd(FLOOR_MAJOR).minor_units,
  };
});

export const forecastFixture: ForecastSnapshot = {
  company: "NovaTech Industries",
  currency: "USD",
  forecast_version_id: "fv-2026-W10",
  published: false,
  as_of: "2026-03-02T09:00:00Z",
  synthetic: true,

  liquidity: {
    cash_today: usd(24_800_000),
    forecast_min_cash: usd(18_400_000),
    forecast_min_week: 6,
    floor: usd(FLOOR_MAJOR),
    runway_weeks: 19,
    revolver_available: usd(8_000_000),
    revolver_utilization_pct: "42.0",
  },

  weeks: WEEKS,

  rows: ROWS,

  variance_bridge: [
    {
      category: "AR collections — enterprise",
      plan: usd(5_600_000),
      actual: usd(4_200_000),
      delta: usd(-1_400_000),
      explanation:
        "Contoso invoice 10482 ($1.2M) slipped past terms after a disputed delivery note; the remainder is timing within the same week.",
      explained_by: "variance",
      evidence_reference: "ar_ledger:INV-10482#amount_due",
    },
    {
      category: "Dodo subscription receipts",
      plan: usd(2_100_000),
      actual: usd(1_900_000),
      delta: usd(-200_000),
      explanation:
        "Soft declines up 3.1pp on renewal cohort; $140K sits inside the documented retry window and is recoverable.",
      explained_by: "dodo_revenue",
      evidence_reference: "dodo:decline-2026-W10#soft_rate",
    },
    {
      category: "AP — vendor payments",
      plan: usd(2_800_000),
      actual: usd(3_100_000),
      delta: usd(-300_000),
      explanation: "Payment run executed two days early to capture a 2/10 discount worth $58K.",
      explained_by: "variance",
      evidence_reference: "ap_ledger:run-2026-W09",
    },
  ],

  accuracy: [
    { horizon_weeks: 1, mape_pct: "2.1", sample_size: 26 },
    { horizon_weeks: 4, mape_pct: "5.8", sample_size: 26 },
    { horizon_weeks: 6, mape_pct: "8.4", sample_size: 26 },
    { horizon_weeks: 13, mape_pct: "14.2", sample_size: 26 },
  ],

  exceptions: [
    {
      id: "exc-1",
      severity: "critical",
      headline: "Minimum cash breached at W6: $18.4M against a $20.0M floor",
      detail:
        "Policy min-cash is a hard constraint. A war room opens on this condition unless the shortfall is closed.",
      week_index: 6,
      reference: "policy:treasury-policy-v4#min_cash",
    },
    {
      id: "exc-2",
      severity: "warning",
      headline: "Dodo recovery-rate assumption is stale from W5",
      detail:
        "The driver was last refreshed 19 days ago and the decline mix has moved since. Review before publishing.",
      week_index: 5,
      reference: "dodo:decline-2026-W10#soft_rate",
    },
    {
      id: "exc-3",
      severity: "warning",
      headline: "Contoso invoice 10482 is 41 days past terms",
      detail:
        "$1.2M, no payment commitment on file. The AR agent ranks this first on the collections worklist.",
      reference: "ar_ledger:INV-10482#amount_due",
    },
  ],
};
