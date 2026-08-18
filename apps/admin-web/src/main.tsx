import React from "react";
import ReactDOM from "react-dom/client";
import "@pkuba/design-tokens/tokens.css";

import { AdminWorkspace } from "./AdminWorkspace";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AdminWorkspace />
  </React.StrictMode>,
);
