import { createContext, useContext, useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, get, post, setCsrfToken } from "../shared/api/client";
import type { Session } from "../shared/api/contracts";

type SessionContextValue = {
  session: Session | null;
  loading: boolean;
  error: Error | null;
  refresh: () => void;
  authenticate: (session: Session) => void;
  logout: () => Promise<void>;
};
const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["session"],
    queryFn: async ({ signal }) => {
      try {
        const session = await get<Session>("/auth/session", signal);
        setCsrfToken(session.csrf_token);
        return session;
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          setCsrfToken("");
          return null;
        }
        throw error;
      }
    },
    retry: false,
    staleTime: 60_000,
  });
  const previousRights = useRef("");
  const rights = JSON.stringify(query.data?.workspaces ?? []);
  useEffect(() => {
    if (previousRights.current && previousRights.current !== rights)
      void client.resetQueries({ queryKey: ["workspace"] });
    previousRights.current = rights;
  }, [client, rights]);
  useEffect(() => {
    const expired = () => {
      setCsrfToken("");
      void client.cancelQueries();
      client.removeQueries({
        predicate: (query) => query.queryKey[0] !== "session",
      });
      client.setQueryData(["session"], null);
    };
    const revoked = () => {
      void client.invalidateQueries({ queryKey: ["session"] });
    };
    window.addEventListener("session-expired", expired);
    window.addEventListener("access-revoked", revoked);
    return () => {
      window.removeEventListener("session-expired", expired);
      window.removeEventListener("access-revoked", revoked);
    };
  }, [client]);
  const authenticate = (session: Session) => {
    void client.cancelQueries();
    client.removeQueries({
      predicate: (query) => query.queryKey[0] !== "session",
    });
    setCsrfToken(session.csrf_token);
    client.setQueryData(["session"], session);
  };
  const logout = async () => {
    await post("/auth/logout");
    setCsrfToken("");
    await client.cancelQueries();
    client.removeQueries({
      predicate: (query) => query.queryKey[0] !== "session",
    });
    client.setQueryData(["session"], null);
  };
  return (
    <SessionContext.Provider
      value={{
        session: query.data ?? null,
        loading: query.isPending,
        error: query.error,
        refresh: () => {
          void query.refetch();
        },
        authenticate,
        logout,
      }}
    >
      {children}
    </SessionContext.Provider>
  );
}

export function useSession() {
  const value = useContext(SessionContext);
  if (!value) throw new Error("SessionProvider is missing");
  return value;
}
