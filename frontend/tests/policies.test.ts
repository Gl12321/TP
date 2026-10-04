import { describe, expect, it } from "vitest";
import {
  policyDraft,
  validatePolicies,
} from "../src/features/data/SourcesPanel";
import type { CatalogTable } from "../src/shared/api/contracts";

const table: CatalogTable = {
  schema: "sales",
  name: "orders",
  columns: [
    { name: "store_code", data_type: "text" },
    { name: "amount", data_type: "numeric" },
  ],
  policy: null,
};

describe("catalog access editor", () => {
  it("does not enable tables or fields by default", () =>
    expect(policyDraft([table])[0]).toMatchObject({
      enabled: false,
      columns: [],
      shared: false,
      store_column: null,
    }));
  it("rejects a fact without an allowed boundary key", () => {
    const draft = policyDraft([table]);
    draft[0] = {
      ...draft[0],
      enabled: true,
      columns: ["amount"],
      store_column: "store_code",
    };
    expect(validatePolicies(draft)).toContain("sales.orders");
  });
  it("accepts only an explicit shared dimension or an allowed store boundary", () => {
    const draft = policyDraft([table]);
    draft[0] = {
      ...draft[0],
      enabled: true,
      columns: ["amount"],
      shared: true,
    };
    expect(validatePolicies(draft)).toBeNull();
    draft[0] = {
      ...draft[0],
      shared: false,
      columns: ["amount", "store_code"],
      store_column: "store_code",
    };
    expect(validatePolicies(draft)).toBeNull();
  });
  it("allows disabling every table", () =>
    expect(validatePolicies(policyDraft([table]))).toBeNull());
});
