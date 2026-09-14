// @vitest-environment jsdom
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import DocumentViewer from "./components/DocumentViewer";

const fields = [
  { field_name: "vendor_name", box: [0.1, 0.2, 0.3, 0.25] },
  { field_name: "total", box: [0.6, 0.8, 0.7, 0.85] },
  { field_name: "notes", box: null },
];

describe("DocumentViewer", () => {
  beforeEach(() => cleanup());
  it("renders a box only for fields that have one", () => {
    render(<DocumentViewer imageUrl="blob:x" fields={fields} activeField={null} onPickField={() => {}} />);
    expect(screen.getByTestId("box-vendor_name")).toBeTruthy();
    expect(screen.getByTestId("box-total")).toBeTruthy();
    expect(screen.queryByTestId("box-notes")).toBeNull();
  });

  it("calls onPickField when a box is clicked", () => {
    const onPick = vi.fn();
    render(<DocumentViewer imageUrl="blob:x" fields={fields} activeField={null} onPickField={onPick} />);
    fireEvent.click(screen.getByTestId("box-total"));
    expect(onPick).toHaveBeenCalledWith("total");
  });

  it("marks the active field's box", () => {
    render(<DocumentViewer imageUrl="blob:x" fields={fields} activeField="vendor_name" onPickField={() => {}} />);
    expect(screen.getByTestId("box-vendor_name").getAttribute("data-active")).toBe("true");
  });
});
