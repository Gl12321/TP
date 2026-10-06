import { useEffect, useState } from "react";

export function useDebouncedValue<T>(value: T, delay = 250) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timeout = window.setTimeout(() => setSettled(value), delay);
    return () => window.clearTimeout(timeout);
  }, [value, delay]);
  return settled;
}
