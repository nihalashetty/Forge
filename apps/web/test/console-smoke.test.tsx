import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getProject: vi.fn(),
      listSecrets: vi.fn(),
      listComponents: vi.fn(),
      listWorkflows: vi.fn(),
      createRun: vi.fn(),
      runStreamUrl: vi.fn(),
      listVersions: vi.fn(),
      restoreVersion: vi.fn(),
    },
    openSSE: vi.fn(),
  };
});

import { SettingsScreen } from "@/components/screens/settings";
import { PlaygroundScreen } from "@/components/screens/playground";
import { guardCanvasBeforeUnload } from "@/components/screens/workflows";
import { VersionHistory } from "@/components/version-history";
import { api, openSSE } from "@/lib/api";

const apiMock = vi.mocked(api);
const openSSEMock = vi.mocked(openSSE);

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.getProject.mockResolvedValue({
    id: "p1", name: "Forge", slug: "forge", description: "", status: "active", config: {},
  });
  apiMock.listSecrets.mockResolvedValue([]);
  apiMock.listComponents.mockResolvedValue([]);
  apiMock.listWorkflows.mockResolvedValue([
    { id: "wf1", project_id: "p1", name: "Support", status: "active", active_version: 1, executable: {}, canvas: {} },
  ]);
  apiMock.createRun
    .mockResolvedValueOnce({ id: "run1", status: "queued", thread_id: "thread-1" })
    .mockResolvedValueOnce({ id: "run2", status: "queued", thread_id: "thread-2" });
  apiMock.runStreamUrl.mockImplementation((_pid, _wid, runId) => `/stream/${runId}`);
  openSSEMock.mockImplementation(async (_url, onFrame) => {
    onFrame({ event: "done", data: { answer: "Done", total_tokens: 1, total_cost_usd: 0 } });
  });
});

describe("Forge console smoke coverage", () => {
  it("navigates to the Versioning settings section", async () => {
    const user = userEvent.setup();
    render(<SettingsScreen project={{ id: "p1" }} />);

    await waitFor(() => expect(apiMock.getProject).toHaveBeenCalledWith("p1"));
    await user.click(screen.getByRole("button", { name: "Versioning" }));

    expect(screen.getByText("Version history")).toBeInTheDocument();
    expect(screen.getByText("Versions kept per entity")).toBeInTheDocument();
  });

  it("Playground reset clears the server-side thread handle", async () => {
    const user = userEvent.setup();
    render(<PlaygroundScreen project={{ id: "p1" }} />);

    const composer = await screen.findByPlaceholderText("Message the workflow…");
    await user.type(composer, "first turn");
    await user.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(apiMock.createRun).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: "Reset" }));
    await user.type(composer, "fresh turn");
    await user.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(apiMock.createRun).toHaveBeenCalledTimes(2));

    expect(apiMock.createRun.mock.calls[1][3]).toBeUndefined();
  });

  it("lists and restores a prior entity version", async () => {
    const user = userEvent.setup();
    apiMock.listVersions.mockResolvedValue([
      { id: "v2", version_no: 2, label: "Current", author_email: "dev@forge.test" },
      { id: "v1", version_no: 1, label: "Before prompt edit", author_email: "dev@forge.test" },
    ]);
    apiMock.restoreVersion.mockResolvedValue({ ok: true } as never);
    const onRestored = vi.fn();
    render(<VersionHistory entityType="workflow" entityId="wf1" onRestored={onRestored} />);

    await user.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByText("Before prompt edit")).toBeInTheDocument();
    await user.click(screen.getByTitle("Restore v1"));

    await waitFor(() => expect(apiMock.restoreVersion).toHaveBeenCalledWith("workflow", "wf1", 1));
    expect(onRestored).toHaveBeenCalledOnce();
  });

  it("blocks tab unload only when the canvas is dirty", () => {
    const clean = new Event("beforeunload", { cancelable: true }) as BeforeUnloadEvent;
    const dirty = new Event("beforeunload", { cancelable: true }) as BeforeUnloadEvent;

    expect(guardCanvasBeforeUnload(false, clean)).toBe(false);
    expect(clean.defaultPrevented).toBe(false);
    expect(guardCanvasBeforeUnload(true, dirty)).toBe(true);
    expect(dirty.defaultPrevented).toBe(true);

    // The helper used by WorkflowCanvas also behaves correctly as an actual event listener.
    const listener = (event: Event) => guardCanvasBeforeUnload(true, event as BeforeUnloadEvent);
    window.addEventListener("beforeunload", listener);
    const dispatched = fireEvent(window, new Event("beforeunload", { cancelable: true }));
    window.removeEventListener("beforeunload", listener);
    expect(dispatched).toBe(false);
  });
});
