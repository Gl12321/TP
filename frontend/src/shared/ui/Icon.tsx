const paths = {
  overview: "M3 12h7V3H3v9Zm0 9h7v-6H3v6Zm11 0h7v-9h-7v9Zm0-18v6h7V3h-7Z",
  stores: "M3 10V5h18v5M3 10h18M5 10v11h14V10M9 21v-7h6v7M2 5l2-3h16l2 3",
  assistant: "m12 3 2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2L12 3Z",
  reports: "M5 3h10l4 4v14H5V3Zm9 0v5h5M8 12h8M8 16h8",
  cases:
    "M21 11.5a8.3 8.3 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.3 8.3 0 0 1-3.8-.9L3 21l1.9-5.7a8.3 8.3 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.3 8.3 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z",
  data: "M20 6c0 2-3.6 3-8 3S4 8 4 6s3.6-3 8-3 8 1 8 3ZM4 6v12c0 2 3.6 3 8 3s8-1 8-3V6M4 12c0 2 3.6 3 8 3s8-1 8-3",
  settings:
    "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2",
  bell: "M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4",
  close: "m6 6 12 12M6 18 18 6",
  arrow: "M5 12h14m-6-6 6 6-6 6",
  back: "M19 12H5m6-6-6 6 6 6",
  chevron: "m9 5 7 7-7 7",
  down: "m6 9 6 6 6-6",
  plus: "M12 5v14M5 12h14",
  search: "M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15Zm6 13 5 5",
  logout: "M9 4H4v16h5M10 12h11m-5-5 5 5-5 5",
  download: "M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5",
  copy: "M9 9h12v12H9V9ZM15 5V3H3v12h2",
  check: "m4 12 5 5L20 6",
  alert: "m12 3 10 18H2L12 3Zm0 6v5m0 3v1",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 4v5l3 2",
  refresh:
    "M20 7v5h-5M4 17v-5h5M6.1 6.1a8 8 0 0 1 13 2M4.9 15.9a8 8 0 0 0 13 2",
  menu: "M4 6h16M4 12h16M4 18h16",
  lock: "M6 10h12v11H6V10Zm2 0V6a4 4 0 1 1 8 0v4",
  calendar: "M5 4h14v17H5V4ZM8 2v4m8-4v4M5 9h14",
  send: "m22 2-7 20-4-9-9-4 20-7ZM22 2 11 13",
  stop: "M6 6h12v12H6V6Z",
  expand: "M8 3H3v5M16 3h5v5M21 16v5h-5M3 16v5h5",
} as const;

export type IconName = keyof typeof paths;
export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}
