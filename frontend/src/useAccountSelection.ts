import { useEffect, useRef, useState } from "react";
import { api, type Account } from "./api";

export type AccountFilters = {
  q: string;
  tier?: string | null;
  group_id?: string | null;
  statuses: string;
};
export function useAccountSelection(filters: AccountFilters, epoch: number) {
  const scope = JSON.stringify(filters);
  const [active, setActive] = useState(false);
  const [ids, setIds] = useState<string[]>([]);
  const current = useRef(ids);
  current.current = ids;
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [verified, setVerified] = useState(scope);
  const generation = useRef(0);
  const request = useRef<AbortController | null>(null);
  function interrupt() {
    generation.current++;
    request.current?.abort();
    request.current = null;
  }
  function replace(next: string[]) {
    interrupt();
    setPending(false);
    current.current = next;
    setIds(next);
    setError("");
  }
  async function resolve(all: boolean) {
    interrupt();
    const token = generation.current;
    const controller = new AbortController();
    request.current = controller;
    setPending(true);
    setError("");
    try {
      const result = await api<{ account_ids: string[] }>(
        "/account-selections",
        "POST",
        {
          filters,
          ...(all ? {} : { account_ids: current.current }),
        },
        { signal: controller.signal },
      );
      if (token !== generation.current) return;
      current.current = result.account_ids;
      setIds(result.account_ids);
      setVerified(scope);
    } catch (e) {
      if (token === generation.current && !controller.signal.aborted)
        setError((e as Error).message);
    } finally {
      if (token === generation.current) {
        setPending(false);
        request.current = null;
      }
    }
  }
  useEffect(() => {
    if (!active) return;
    if (current.current.length) void resolve(false);
    else {
      interrupt();
      setVerified(scope);
      setPending(false);
    }
    return interrupt;
    // A change of selection itself is handled synchronously by replace().
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, scope, epoch]);
  useEffect(() => () => interrupt(), []);
  const busy = pending || (active && ids.length > 0 && verified !== scope);
  return {
    active,
    ids,
    busy,
    error,
    enter() {
      replace([]);
      setVerified(scope);
      setActive(true);
    },
    exit() {
      replace([]);
      setActive(false);
    },
    clear() {
      replace([]);
      setVerified(scope);
    },
    all() {
      if (!busy) void resolve(true);
    },
    retry() {
      void resolve(false);
    },
    toggle(id: string, checked: boolean) {
      if (!busy)
        replace(
          checked
            ? [...new Set([...current.current, id])]
            : current.current.filter((item) => item !== id),
        );
    },
    page(rows: Account[], checked: boolean) {
      if (busy) return;
      const pageIds = new Set(rows.map((row) => row.id));
      replace(
        checked
          ? [...new Set([...current.current, ...pageIds])]
          : current.current.filter((id) => !pageIds.has(id)),
      );
    },
  };
}
