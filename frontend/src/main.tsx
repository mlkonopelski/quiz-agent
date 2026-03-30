import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { AppShell } from "./screens/AppShell";
import "./index.css";

const container = document.getElementById("root");

if (!container) {
  throw new Error("Root container was not found.");
}

createRoot(container).render(
  <StrictMode>
    <AppShell />
  </StrictMode>,
);
