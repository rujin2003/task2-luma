"use client";

import { useEffect, useState } from "react";

import { EVENT_TYPES, MARK_GLYPH, type StatusMark, type WarRoomEvent } from "@/lib/events";
import { apiUrl, fetchJson } from "@/lib/api";

type FeedEvent = WarRoomEvent & { mark: StatusMark; status_line: string; seq: number };

function ingest(setEvents: React.Dispatch<React.SetStateAction<FeedEvent[]>>, raw: string) {
  try {
    const parsed = JSON.parse(raw) as FeedEvent;
    if (parsed.type === "stream.opened" || parsed.type === "stream.heartbeat") return;
    setEvents((prev) => {
      if (prev.some((e) => e.seq === parsed.seq)) return prev;
      return [...prev, parsed].sort((a, b) => a.seq - b.seq);
    });
  } catch {
    /* ignore malformed frames */
  }
}

export function WarRoomFeed() {
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const history = await fetchJson<FeedEvent[]>("/api/events/history");
        if (!cancelled) setEvents(history);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load events");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const source = new EventSource(apiUrl("/api/events"));
    const onFrame = (message: MessageEvent<string>) => ingest(setEvents, message.data);
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, onFrame as EventListener);
    }
    source.onerror = () => {
      setError((prev) => prev ?? "Live stream reconnecting…");
    };
    return () => source.close();
  }, []);

  async function runCycle() {
    setRunning(true);
    setError(null);
    try {
      await fetchJson("/api/cycle/run", {
        method: "POST",
        body: JSON.stringify({ as_of: "2026-03-02", auto_investigate: true }),
      });
      const history = await fetchJson<FeedEvent[]>("/api/events/history");
      setEvents(history);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cycle failed");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="mx-auto flex max-w-[1100px] flex-col gap-4 px-4 py-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-ink">War Room</h1>
          <p className="mt-1 text-sm text-ink-2">
            Live investigation feed. Findings, evidence, decisions — no chain-of-thought.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void runCycle()}
          disabled={running}
          className="border border-line bg-surface-2 px-3 py-2 text-xs font-medium text-ink hover:border-accent disabled:opacity-50"
        >
          {running ? "Running cycle…" : "Run Monday cycle"}
        </button>
      </div>

      {error ? <p className="text-sm text-warning">{error}</p> : null}

      <ol className="flex flex-col gap-1 border border-line bg-surface-1 p-3 font-mono text-xs">
        {events.length === 0 ? (
          <li className="text-ink-3">No activity yet. Run the Monday cycle to open the war room.</li>
        ) : (
          events.map((event) => (
            <li key={event.seq} className="flex gap-2 text-ink-2">
              <span className="w-4 shrink-0 text-ink">{MARK_GLYPH[event.mark] ?? "•"}</span>
              <span className="w-8 shrink-0 text-ink-3">{event.seq}</span>
              <span className="text-ink">{event.status_line}</span>
            </li>
          ))
        )}
      </ol>
    </div>
  );
}
