import { useEffect, useRef, useState } from "react";

export function useMobileNavigation(pathname: string) {
  const [mobileViewport, setMobileViewport] = useState(
    () => window.matchMedia("(max-width: 820px)").matches,
  );
  const [mobileOpen, setMobileOpen] = useState(false);
  const main = useRef<HTMLElement>(null);
  const sidebar = useRef<HTMLElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const drawerOpen = mobileViewport && mobileOpen;
  const closeNavigation = () => {
    setMobileOpen(false);
    requestAnimationFrame(() => menuButton.current?.focus());
  };
  const navigateFromSidebar = () => {
    if (!drawerOpen) return;
    setMobileOpen(false);
    requestAnimationFrame(() => main.current?.focus({ preventScroll: true }));
  };
  useEffect(() => {
    const media = window.matchMedia("(max-width: 820px)");
    let frame = 0;
    const update = () => {
      const focusInSidebar = sidebar.current?.contains(document.activeElement);
      const focusOnMenu = document.activeElement === menuButton.current;
      const focusOnClose = document.activeElement?.matches(".mobile-nav-close");
      setMobileViewport(media.matches);
      setMobileOpen(false);
      cancelAnimationFrame(frame);
      if (media.matches && focusInSidebar)
        frame = requestAnimationFrame(() => menuButton.current?.focus());
      else if (!media.matches && (focusOnMenu || focusOnClose))
        frame = requestAnimationFrame(() => main.current?.focus());
    };
    media.addEventListener("change", update);
    return () => {
      cancelAnimationFrame(frame);
      media.removeEventListener("change", update);
    };
  }, []);
  useEffect(() => {
    setMobileOpen(false);
    const frame = requestAnimationFrame(() =>
      main.current?.focus({ preventScroll: true }),
    );
    return () => cancelAnimationFrame(frame);
  }, [pathname]);
  useEffect(() => {
    if (!drawerOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    sidebar.current?.querySelector<HTMLElement>("a,button,select")?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setMobileOpen(false);
        requestAnimationFrame(() => menuButton.current?.focus());
        return;
      }
      if (event.key !== "Tab") return;
      const elements = Array.from(
        sidebar.current?.querySelectorAll<HTMLElement>(
          "a[href],button:not(:disabled),select:not(:disabled)",
        ) ?? [],
      ).filter((element) => element.getClientRects().length > 0);
      if (!elements?.length) return;
      const first = elements[0],
        last = elements[elements.length - 1];
      if (!sidebar.current?.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", keydown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", keydown);
    };
  }, [drawerOpen]);
  return {
    main,
    sidebar,
    menuButton,
    mobileViewport,
    drawerOpen,
    openNavigation: () => setMobileOpen(true),
    closeNavigation,
    navigateFromSidebar,
  };
}
