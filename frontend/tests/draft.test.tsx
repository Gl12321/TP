import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it } from "vitest";
import { WorkspaceProvider } from "../src/app/workspace";
import { useDraft } from "../src/shared/ui/useDraft";

function Editor({ name }: { name: string }) {
  const [value, setValue] = useDraft(name, "");
  return (
    <input
      aria-label="Черновик"
      value={value}
      onChange={(event) => setValue(event.target.value)}
    />
  );
}
function Harness() {
  const [name, setName] = useState("comment");
  return (
    <>
      <button
        onClick={() => setName(name === "comment" ? "conclusion" : "comment")}
      >
        Сменить раздел
      </button>
      <Editor key={name} name={name} />
    </>
  );
}
it("retains separate unsent drafts across navigation within the authenticated workspace cache", async () => {
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <WorkspaceProvider
        workspace={{
          id: "w",
          name: "Сеть",
          role: "director",
          capabilities: [],
          all_stores: true,
          store_ids: [],
        }}
      >
        <Harness />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Черновик"), "Комментарий");
  await user.click(screen.getByText("Сменить раздел"));
  expect(screen.getByLabelText("Черновик")).toHaveValue("");
  await user.type(screen.getByLabelText("Черновик"), "Итог");
  await user.click(screen.getByText("Сменить раздел"));
  expect(screen.getByLabelText("Черновик")).toHaveValue("Комментарий");
});
