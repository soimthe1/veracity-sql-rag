import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { AuthProvider } from "./auth";
import { ThemeProvider } from "./theme";
import { I18nProvider } from "./i18n";
import { LatticeProvider } from "./lib/lattice";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <I18nProvider>
        <LatticeProvider>
          <AuthProvider>
            <App />
          </AuthProvider>
        </LatticeProvider>
      </I18nProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
