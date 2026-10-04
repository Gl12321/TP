import { createContext, useContext, useState, useCallback } from "react";
import type { ReactNode } from "react";
import { Icon } from "./Icon";

const ToastContext = createContext<(message: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<{ id: number; message: string }[]>([]);
  const notify = useCallback((message: string) => {
    const id = Date.now() + Math.random();
    setItems((previous) => [...previous.slice(-2), { id, message }]);
    window.setTimeout(
      () => setItems((previous) => previous.filter((item) => item.id !== id)),
      6000,
    );
  }, []);
  return (
    <ToastContext.Provider value={notify}>
      {children}
      <div className="toast-stack" aria-live="polite" aria-atomic="false">
        {items.map((item) => (
          <div className="toast" key={item.id}>
            <Icon name="check" size={17} />
            <span>{item.message}</span>
            <button
              type="button"
              className="icon-button"
              aria-label="Закрыть уведомление"
              onClick={() =>
                setItems((previous) =>
                  previous.filter((value) => value.id !== item.id),
                )
              }
            >
              <Icon name="close" size={15} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
