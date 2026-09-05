import { AccuracyPanel } from "@/components/AccuracyPanel";
import { ExceptionsQueue } from "@/components/ExceptionsQueue";
import { ForecastGrid } from "@/components/ForecastGrid";
import { LiquidityHeader } from "@/components/LiquidityHeader";
import { ReviewControls } from "@/components/ReviewControls";
import { VarianceBridge } from "@/components/VarianceBridge";
import { forecastFixture } from "@/fixtures/forecast";

/**
 * The Forecast screen -- the analyst's home, and the primary surface of the product.
 * Rendered against fixtures until the engine is behind the tool layer; the shape of the
 * data is the contract, so swapping the source changes nothing here.
 */
export default function ForecastScreen() {
  const snapshot = forecastFixture;

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-ink">Forecast</h1>
        <p className="font-mono text-xs text-ink-3">
          as of {snapshot.as_of} · {snapshot.currency}
        </p>
      </div>

      <LiquidityHeader liquidity={snapshot.liquidity} />

      <ForecastGrid snapshot={snapshot} />

      <div className="grid gap-4 lg:grid-cols-3">
        <VarianceBridge items={snapshot.variance_bridge} />
        <ExceptionsQueue exceptions={snapshot.exceptions} />
        <div className="flex flex-col gap-4">
          <AccuracyPanel points={snapshot.accuracy} />
          <ReviewControls versionId={snapshot.forecast_version_id} published={snapshot.published} />
        </div>
      </div>
    </div>
  );
}
