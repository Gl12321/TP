import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { Modal } from "../src/shared/ui/Common";

function Screen({ removeTrigger = false }: { removeTrigger?: boolean }) {
  const [open, setOpen] = useState(false);
  const [removed, setRemoved] = useState(false);
  return (
    <main tabIndex={-1}>
      {!removed && <button onClick={() => setOpen(true)}>Открыть</button>}
      {open && (
        <Modal open onOpenChange={setOpen} title="Параметры">
          <button
            onClick={() => {
              setRemoved(removeTrigger);
              setOpen(false);
            }}
          >
            Сохранить
          </button>
        </Modal>
      )}
    </main>
  );
}

it("returns keyboard focus to the invoking control after a modal closes", async () => {
  render(<Screen />);
  const user = userEvent.setup();
  const trigger = screen.getByRole("button", { name: "Открыть" });
  await user.click(trigger);
  await screen.findByRole("dialog");
  await user.keyboard("{Escape}");
  await waitFor(() => expect(trigger).toHaveFocus());
});

it("focuses main content when saving removes the invoking control", async () => {
  render(<Screen removeTrigger />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Открыть" }));
  await user.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(screen.getByRole("main")).toHaveFocus());
});
