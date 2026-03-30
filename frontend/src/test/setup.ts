import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

Object.defineProperty(HTMLElement.prototype, "scrollTo", {
  configurable: true,
  value: vi.fn(),
});
