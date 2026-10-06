import type { Capability } from "../../../shared/api/contracts";
import type { IconName } from "../../../shared/ui/Icon";

export const navigation: {
  path: string;
  label: string;
  icon: IconName;
  capability: Capability[];
}[] = [
  {
    path: "overview",
    label: "Обзор",
    icon: "overview",
    capability: ["analytics:read"],
  },
  {
    path: "stores",
    label: "Точки",
    icon: "stores",
    capability: ["analytics:read"],
  },
  {
    path: "assistant",
    label: "Ассистент",
    icon: "assistant",
    capability: ["assistant:use"],
  },
  {
    path: "reports",
    label: "Отчёты",
    icon: "reports",
    capability: ["analytics:read"],
  },
  {
    path: "cases",
    label: "Разборы",
    icon: "cases",
    capability: ["analytics:read"],
  },
  {
    path: "data",
    label: "Данные",
    icon: "data",
    capability: ["sources:manage", "analytics:read"],
  },
];
